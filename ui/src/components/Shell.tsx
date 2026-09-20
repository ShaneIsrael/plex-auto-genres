import { History, LayoutDashboard, Library, Link2, LogOut, Settings2 } from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuthStatus, useLogout } from "../api/client";
import { useConfirm } from "./ConfirmDialog";
import { StatusStrip } from "./StatusStrip";
import { useUnsaved } from "./UnsavedGuard";

const NAV = [
  { to: "/", n: "01", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/runs", n: "02", label: "Runs", icon: History },
  { to: "/libraries", n: "03", label: "Libraries", icon: Library },
  { to: "/bindings", n: "04", label: "Bindings", icon: Link2 },
  { to: "/config", n: "05", label: "Config", icon: Settings2 },
];

export function Shell() {
  const { dirty } = useUnsaved();
  const confirm = useConfirm();
  const navigate = useNavigate();
  const auth = useAuthStatus();
  const logout = useLogout();
  const canSignOut = auth.data?.enabled === true;

  // Leaving a page with unsaved edits asks first; the browser's own
  // beforeunload covers reloads and closed tabs.
  const guard = (to: string) => async (e: React.MouseEvent) => {
    if (!dirty) return;
    e.preventDefault();
    const ok = await confirm({ title: "Leave without saving?", body: "Your edits to the config will be lost.", confirmLabel: "Leave", danger: true });
    if (ok) navigate(to);
  };

  return (
    <div className="shell">
      <a className="skip" href="#main">Skip to content</a>
      <nav className="rail" aria-label="Primary">
        <NavLink to="/" className="brand" end>
          <span className="brand__mark" aria-hidden="true">
            <svg viewBox="0 0 32 32" width="28" height="28">
              <rect x="3" y="3" width="26" height="26" rx="4" fill="none" stroke="var(--amber)" strokeWidth="2" />
              <circle cx="16" cy="16" r="5" fill="var(--teal)" />
            </svg>
          </span>
          <span className="brand__text">
            <span className="brand__name">plex-auto-genres</span>
            <span className="brand__sub label">master control</span>
          </span>
        </NavLink>

        <ul className="nav">
          {NAV.map(({ to, n, label, icon: Icon, end }) => (
            <li key={to}>
              <NavLink to={to} end={end} onClick={guard(to)} className={({ isActive }) => `nav__item${isActive ? " is-active" : ""}`}>
                <span className="nav__n mono" aria-hidden="true">{n}</span>
                <Icon className="nav__icon" size={16} strokeWidth={1.75} aria-hidden="true" />
                <span className="nav__label">{label}</span>
              </NavLink>
            </li>
          ))}
        </ul>

        <div className="rail__foot mono faint">
          <a href="/api/docs" target="_blank" rel="noreferrer">API docs ↗</a>
          {canSignOut && (
            <button type="button" className="rail__signout" onClick={() => logout.mutate()} disabled={logout.isPending}>
              <LogOut size={13} aria-hidden="true" /> Sign out
            </button>
          )}
        </div>
      </nav>

      <div className="main">
        <StatusStrip />
        <main id="main" className="content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
