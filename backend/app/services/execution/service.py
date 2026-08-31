"""Turns an approved proposal into a real GitHub pull request.

    approved
        v
    executing (claimed — see app.repositories.change_repo.claim_for_execution)
        v
    snapshot re-checked, patch re-validated, secret-scanned
        v
    blob(s) -> tree -> commit -> branch          (committed)
        v
    pull request                                  (pr_created)

Every step after the claim uses only what was already validated when the
proposal was created, or re-validates it fresh — the model is never consulted
again, and nothing here can touch a file the original investigation did not
already read and the human did not already approve.

No local git, no subprocess, no shell: everything is GitHub's Git Data API,
so there is no working directory to isolate, no credential that ever touches
a filesystem or a `git remote` URL, and no string built for a shell to
interpret. See the comment above GitHubClient's write-path methods.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError
from app.integrations.github.client import GitHubClient
from app.models.change import ChangeStatus, ProposedChange
from app.models.repository import Repository
from app.repositories import change_repo
from app.services.execution.branching import generate_branch_name
from app.services.execution.secret_scan import scan_edits
from app.services.patching import (
    PatchError,
    StaleSnapshotError,
    assert_snapshot_current,
    parse_edits,
    validate_patch,
)

logger = logging.getLogger(__name__)

_MAX_TITLE_CHARS = 72


class ExecutionConflictError(ConflictError):
    code = "execution_conflict"


def execute_change(
    session: Session,
    *,
    proposal: ProposedChange,
    repository: Repository,
    client: GitHubClient,
) -> ProposedChange:
    """Create a branch, commit, and pull request for one approved proposal.

    Idempotent: calling this again on a proposal that already reached
    ``pr_created`` is a no-op that returns the existing PR. Calling it again on
    one still ``committed`` (a prior PR-creation attempt failed) retries only
    that step, against the branch and commit that already exist.
    """
    if proposal.status == ChangeStatus.PR_CREATED:
        return proposal

    resume_from = change_repo.claim_for_execution(session, proposal.id)
    if resume_from is None:
        session.refresh(proposal)
        if proposal.status == ChangeStatus.EXECUTING:
            raise ExecutionConflictError("This change is already being executed.")
        if proposal.status == ChangeStatus.PR_CREATED:  # type: ignore[comparison-overlap]
            # A concurrent call won the claim and finished between our failed
            # claim attempt and this refresh. Not a failure — same outcome as
            # if we had won it ourselves and it was already done.
            return proposal
        raise ConflictError(
            "This change must be approved before it can be executed.",
            details={"status": str(proposal.status)},
        )

    session.refresh(proposal)
    change_repo.record_event(proposal, "execution_started")
    session.commit()

    try:
        if resume_from == ChangeStatus.APPROVED:
            _build_branch_and_commit(
                session, proposal=proposal, repository=repository, client=client
            )
        _open_pull_request(session, proposal=proposal, repository=repository, client=client)
        return proposal

    except StaleSnapshotError as exc:
        proposal.status = ChangeStatus.STALE
        proposal.execution_error = exc.message
        change_repo.record_event(proposal, "execution_failed")
        session.commit()
        return proposal

    except PatchError as exc:
        return _fail(session, proposal, exc.message)

    except AppError as exc:
        if proposal.status == ChangeStatus.COMMITTED:
            # The commit and branch are real; only PR creation failed. That is
            # retryable from here, so the commit must not be blamed for it.
            proposal.execution_error = exc.message
            change_repo.record_event(proposal, "execution_failed")
            session.commit()
            return proposal
        return _fail(session, proposal, exc.message)

    except Exception:
        logger.exception("Change execution failed unexpectedly change_id=%s", proposal.id)
        message = "An unexpected error occurred while executing this change."
        if proposal.status == ChangeStatus.COMMITTED:
            proposal.execution_error = message
            change_repo.record_event(proposal, "execution_failed")
            session.commit()
            return proposal
        return _fail(session, proposal, message)


def _build_branch_and_commit(
    session: Session, *, proposal: ProposedChange, repository: Repository, client: GitHubClient
) -> None:
    assert_snapshot_current(
        proposal_sha=proposal.indexed_commit_sha, repository_sha=repository.indexed_commit_sha
    )

    edits = parse_edits(proposal.edits)
    validated = validate_patch(
        session,
        repository_id=repository.id,
        edits=edits,
        allowed_paths={edit.path for edit in edits},
    )

    finding = scan_edits(edits)
    if finding is not None:
        raise PatchError(
            f"This change was blocked before committing: {finding.path} appears to contain "
            f"{finding.kind}. Remove it and request the change again.",
            details={"path": finding.path},
        )

    owner, name = repository.owner, repository.name
    base_sha = repository.indexed_commit_sha
    assert base_sha is not None  # guaranteed by assert_snapshot_current above

    base_tree_sha = client.get_commit_tree_sha(owner, name, base_sha)
    tree_entries = [
        {
            "path": file_patch.path,
            "mode": "100644",
            "type": "blob",
            "sha": client.create_blob(owner, name, file_patch.updated),
        }
        for file_patch in validated.files
    ]
    new_tree_sha = client.create_tree(
        owner, name, base_tree_sha=base_tree_sha, entries=tree_entries
    )
    new_commit_sha = client.create_commit(
        owner, name, message=_commit_message(proposal), tree_sha=new_tree_sha, parent_sha=base_sha
    )

    branch = generate_branch_name(proposal.id, proposal.summary)
    client.create_branch(owner, name, branch=branch, commit_sha=new_commit_sha)

    proposal.branch_name = branch
    proposal.commit_sha = new_commit_sha
    proposal.status = ChangeStatus.COMMITTED
    proposal.executed_at = datetime.now(UTC)
    change_repo.record_event(proposal, "branch_created")
    change_repo.record_event(proposal, "patch_applied")
    change_repo.record_event(proposal, "commit_created")
    change_repo.record_event(proposal, "push_completed")
    session.commit()


def _open_pull_request(
    session: Session, *, proposal: ProposedChange, repository: Repository, client: GitHubClient
) -> None:
    assert proposal.branch_name is not None  # guaranteed once COMMITTED

    pr = client.create_pull_request(
        repository.owner,
        repository.name,
        title=_pr_title(proposal),
        body=_pr_body(proposal),
        head=proposal.branch_name,
        base=repository.default_branch,
    )

    proposal.status = ChangeStatus.PR_CREATED
    proposal.pr_number = pr.number
    proposal.pr_url = pr.html_url
    proposal.pr_node_id = str(pr.id)
    change_repo.record_event(proposal, "pr_created")
    session.commit()

    logger.info(
        "Pull request created repository_id=%s change_id=%s pr_number=%d",
        repository.id,
        proposal.id,
        pr.number,
    )


def _fail(session: Session, proposal: ProposedChange, message: str) -> ProposedChange:
    proposal.status = ChangeStatus.FAILED
    proposal.execution_error = message
    change_repo.record_event(proposal, "execution_failed")
    session.commit()
    logger.warning("Change execution failed change_id=%s", proposal.id)
    return proposal


def _commit_message(proposal: ProposedChange) -> str:
    title = _first_line(proposal.summary or proposal.request, _MAX_TITLE_CHARS)
    return f"{title}\n\nGenerated by DevPilot and approved by a human reviewer."


def _pr_title(proposal: ProposedChange) -> str:
    return _first_line(proposal.summary or proposal.request, _MAX_TITLE_CHARS)


def _pr_body(proposal: ProposedChange) -> str:
    files = sorted({edit["path"] for edit in proposal.edits})
    file_list = "\n".join(f"- `{path}`" for path in files) or "- (no files listed)"

    return (
        f"### What changed\n{proposal.summary or '(no summary provided)'}\n\n"
        f"### Files affected\n{file_list}\n\n"
        f"### Why\n{proposal.request}\n\n"
        "### Validation performed\n"
        "Patch validated against the indexed snapshot before this commit was created. "
        "Automated tests were not run — no execution sandbox exists in this deployment.\n\n"
        "---\n"
        "*This change was investigated and proposed by DevPilot's AI agent, reviewed as a "
        "diff, and explicitly approved by a human before anything was written to GitHub.*"
    )


def _first_line(text: str, limit: int) -> str:
    line = text.strip().splitlines()[0] if text.strip() else "Update from DevPilot"
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
