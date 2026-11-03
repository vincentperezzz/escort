import { useEffect, useState } from "react";
import Logo from "../Logo.jsx";
import { Button } from "../ui.jsx";
import { post } from "../../lib/api.js";

export default function FirstProjectOnboarding({ refresh, go }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  async function submit(e) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError("");
    try {
      await post("/api/projects", {
        name: trimmed,
        description: description.trim(),
        activate: true,
      });
      await refresh();
      go("context");
    } catch (err) {
      setError(err.message || "Could not create project");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative flex h-full flex-col">
      <div className="ambient" aria-hidden />
      <div className="relative z-10 flex min-h-0 flex-1 flex-col items-center justify-center px-6 py-12">
        <Logo size={52} />
        <h1 className="mt-8 max-w-md text-center text-[32px] font-semibold leading-tight tracking-[-0.035em] text-balance sm:text-[36px]">
          Create your first project
        </h1>
        <p className="mt-3 max-w-md text-center text-[15px] leading-relaxed text-muted text-pretty">
          A project is your workspace — like a folder for a repo. It holds your files, meetings, and prep chat in one place.
        </p>

        <form onSubmit={submit} className="mt-10 w-full max-w-md space-y-4">
          <div>
            <label htmlFor="onboard-name" className="block text-[12px] font-medium text-muted">
              Project name <span className="text-conflict">*</span>
            </label>
            <input
              id="onboard-name"
              type="text"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Q1 client rollout"
              className="field mt-2 h-11 w-full px-3.5 text-sm"
              required
            />
          </div>
          <div>
            <label htmlFor="onboard-desc" className="block text-[12px] font-medium text-muted">
              Description <span className="font-normal text-faint">(optional)</span>
            </label>
            <textarea
              id="onboard-desc"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Short note for future you"
              className="field scroll-thin mt-2 min-h-[88px] w-full resize-y px-3.5 py-2.5 text-sm leading-relaxed"
            />
          </div>
          {error ? (
            <p className="text-[13px] text-conflict" role="alert">
              {error}
            </p>
          ) : null}
          <Button variant="primary" size="md" type="submit" className="w-full justify-center" disabled={!name.trim() || busy}>
            Create project
          </Button>
        </form>
      </div>
    </div>
  );
}
