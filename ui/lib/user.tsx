"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { describeError, getUsers, type User } from "./api";

const STORAGE_KEY = "internal-brain.userId";
export const DEFAULT_USER_ID = "jdoe";

export interface UserContextValue {
  /** The selected identity, sent as X-User-Id on every request. */
  userId: string;
  setUserId: (id: string) => void;
  /** The directory from GET /users (empty until loaded). */
  users: User[];
  usersLoaded: boolean;
  usersError: string | null;
  reloadUsers: () => void;
  /** The selected user's directory record, if the id is known. */
  currentUser: User | undefined;
  hasRole: (role: string) => boolean;
  /** True once the persisted selection has been read on the client. */
  ready: boolean;
}

const UserContext = createContext<UserContextValue | null>(null);

function readStoredUser(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredUser(id: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // Private mode or blocked storage: the selection simply does not persist.
  }
}

export function UserProvider({ children }: { children: ReactNode }) {
  const [userId, setUserIdState] = useState<string>(DEFAULT_USER_ID);
  const [ready, setReady] = useState(false);
  const [users, setUsers] = useState<User[]>([]);
  const [usersLoaded, setUsersLoaded] = useState(false);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    const stored = readStoredUser();
    if (stored) setUserIdState(stored);
    setReady(true);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setUsersError(null);
    getUsers()
      .then((list) => {
        if (cancelled) return;
        setUsers(list);
        setUsersLoaded(true);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setUsersError(describeError(err));
        setUsersLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadTick]);

  const setUserId = useCallback((id: string) => {
    setUserIdState(id);
    writeStoredUser(id);
  }, []);

  const reloadUsers = useCallback(() => {
    setUsersLoaded(false);
    setReloadTick((t) => t + 1);
  }, []);

  const value = useMemo<UserContextValue>(() => {
    const currentUser = users.find((u) => u.id === userId);
    return {
      userId,
      setUserId,
      users,
      usersLoaded,
      usersError,
      reloadUsers,
      currentUser,
      hasRole: (role: string) => currentUser?.roles.includes(role) ?? false,
      ready,
    };
  }, [userId, setUserId, users, usersLoaded, usersError, reloadUsers, ready]);

  return <UserContext.Provider value={value}>{children}</UserContext.Provider>;
}

export function useUser(): UserContextValue {
  const ctx = useContext(UserContext);
  if (!ctx) throw new Error("useUser must be used inside <UserProvider>");
  return ctx;
}
