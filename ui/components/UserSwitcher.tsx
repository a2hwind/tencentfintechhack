"use client";

import { useUser } from "@/lib/user";
import { RoleBadges } from "./ui";

export function UserSwitcher() {
  const { userId, setUserId, users, usersLoaded, usersError, currentUser, reloadUsers } = useUser();
  const known = users.some((u) => u.id === userId);

  return (
    <div className="switcher">
      <span className="switcher-label">Acting as</span>
      <select className="select" value={userId} onChange={(e) => setUserId(e.target.value)} disabled={!usersLoaded} aria-label="Acting user" title={usersError ?? undefined}>
        {!known && (
          <option value={userId}>
            {userId}
            {usersLoaded ? " (unknown)" : ""}
          </option>
        )}
        {users.map((u) => (
          <option key={u.id} value={u.id}>
            {u.name} — {u.title}
          </option>
        ))}
      </select>
      {currentUser ? <RoleBadges roles={currentUser.roles} /> : null}
      {usersError ? (
        <button type="button" className="btn btn-sm btn-danger" onClick={reloadUsers} title={usersError}>
          Retry
        </button>
      ) : null}
    </div>
  );
}
