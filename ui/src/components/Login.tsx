import { Eye, EyeOff, LogIn } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { ApiError, useLogin } from "../api/client";

export function Login() {
  const id = useId();
  const login = useLogin();
  const [password, setPassword] = useState("");
  const [shown, setShown] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await login.mutateAsync(password);
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      setError(
        status === 401
          ? "That is not the password."
          : status === 429
            ? "Too many attempts — wait a minute, then try again."
            : (err as Error).message,
      );
    }
  };

  return (
    <main className="gate" id="main">
      <form className="login__card reveal" onSubmit={submit} aria-labelledby={`${id}-title`}>
        <div className="login__brand">
          <span className="brand__mark" aria-hidden="true">
            <svg viewBox="0 0 32 32" width="32" height="32">
              <rect x="3" y="3" width="26" height="26" rx="4" fill="none" stroke="var(--amber)" strokeWidth="2" />
              <circle cx="16" cy="16" r="5" fill="var(--teal)" />
            </svg>
          </span>
          <div>
            <div className="label">plex-auto-genres · master control</div>
            <h1 id={`${id}-title`} className="login__title">Sign in</h1>
          </div>
        </div>

        <div className={`field ${error ? "field--invalid" : ""}`}>
          <label className="field__label label" htmlFor={`${id}-pw`}>Password</label>
          <div className="login__row">
            <input
              id={`${id}-pw`}
              className="input"
              type={shown ? "text" : "password"}
              autoComplete="current-password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-describedby={error ? `${id}-err` : undefined}
              aria-invalid={error ? true : undefined}
            />
            <button type="button" className="iconbtn" aria-label={shown ? "Hide password" : "Show password"} aria-pressed={shown} onClick={() => setShown((v) => !v)}>
              {shown ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
            </button>
          </div>
          {error && (
            <div id={`${id}-err`} className="field__error" role="alert">
              {error}
            </div>
          )}
        </div>

        <button type="submit" className="button login__submit" disabled={!password || login.isPending}>
          <LogIn size={14} aria-hidden="true" /> {login.isPending ? "Signing in…" : "Sign in"}
        </button>

        <p className="faint login__foot">
          The password is <code>PAG_WEB_PASSWORD</code> on the server. Sessions last thirty days.
        </p>
      </form>
    </main>
  );
}
