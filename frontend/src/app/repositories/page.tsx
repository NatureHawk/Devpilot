import { redirect } from "next/navigation";

/** The repository list now lives at the start page; old links still arrive there. */
export default function RepositoriesPage() {
  redirect("/");
}
