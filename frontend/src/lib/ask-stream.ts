/**
 * Client-side reader for the answer stream.
 *
 * Parses server-sent events off the response body and reports each increment as
 * it arrives. Nothing is buffered and replayed — a caller rendering these
 * callbacks is watching the model write.
 */

export type AskSource = {
  label: string;
  chunk_id: string;
  file_path: string;
  symbol: string | null;
  chunk_type: string;
  language: string | null;
  start_line: number;
  end_line: number;
  score: number;
  content: string;
};

export type AskStreamHandlers = {
  onSources: (sources: AskSource[], conversationId: string) => void;
  onText: (delta: string) => void;
  onDone: (conversationId: string, messageId: string) => void;
  onError: (code: string, message: string) => void;
};

type AskEvent = {
  type: "sources" | "text" | "done" | "error";
  text?: string;
  sources?: AskSource[];
  conversation_id?: string;
  message_id?: string;
  code?: string;
  message?: string;
};

/**
 * POST a question and drive the handlers until the stream ends.
 *
 * `signal` lets the caller stop an answer in progress; aborting closes the
 * connection rather than merely hiding output.
 */
export async function streamAsk(
  repositoryId: string,
  body: { question: string; conversation_id?: string },
  handlers: AskStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`/api/repositories/${encodeURIComponent(repositoryId)}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      // exactOptionalPropertyTypes rejects an explicit undefined signal.
      ...(signal ? { signal } : {}),
    });
  } catch (error) {
    if ((error as Error)?.name === "AbortError") return;
    handlers.onError("unreachable", "Could not reach DevPilot.");
    return;
  }

  // A pre-stream failure arrives as JSON, in the standard error envelope.
  if (!response.ok || !response.body) {
    const failure = await response
      .json()
      .catch(() => ({ error: { code: "http_error", message: "The request failed." } }));
    handlers.onError(
      failure?.error?.code ?? "http_error",
      failure?.error?.message ?? "The request failed.",
    );
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line. A partial frame stays in the
      // buffer until the rest of it arrives.
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        const line = frame.split("\n").find((candidate) => candidate.startsWith("data: "));
        if (!line) continue;

        let event: AskEvent;
        try {
          event = JSON.parse(line.slice("data: ".length)) as AskEvent;
        } catch {
          continue;
        }

        if (event.type === "sources" && event.sources) {
          handlers.onSources(event.sources, event.conversation_id ?? "");
        } else if (event.type === "text" && event.text) {
          handlers.onText(event.text);
        } else if (event.type === "done") {
          handlers.onDone(event.conversation_id ?? "", event.message_id ?? "");
        } else if (event.type === "error") {
          handlers.onError(event.code ?? "internal_error", event.message ?? "Generation failed.");
        }
      }
    }
  } catch (error) {
    if ((error as Error)?.name !== "AbortError") {
      handlers.onError("stream_failed", "The connection was interrupted.");
    }
  }
}
