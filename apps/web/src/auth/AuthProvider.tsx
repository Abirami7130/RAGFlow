import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setSession } from "../lib/api";
import type { Session, User } from "../lib/types";

type AuthValue = {
  session: Session | null;
  user: User | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({
  children,
  initialSession = null,
}: {
  children: ReactNode;
  initialSession?: Session | null;
}) {
  const [session, setSessionState] = useState<Session | null>(initialSession);
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setSession(initialSession);
    if (!initialSession) {
      setReady(true);
      return;
    }
    api<User>("/api/v1/auth/me")
      .then(setUser)
      .catch(() => {
        setSession(null);
        setSessionState(null);
      })
      .finally(() => setReady(true));
  }, [initialSession]);

  function remember(next: Session | null) {
    setSession(next);
    setSessionState(next);
  }

  const value: AuthValue = {
    session,
    user,
    ready,
    async login(email, password) {
      const body = await api<{ access_token: string; refresh_token: string }>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      const next = { accessToken: body.access_token, refreshToken: body.refresh_token };
      remember(next);
      setUser(await api<User>("/api/v1/auth/me"));
      setReady(true);
    },
    async register(email, password, displayName) {
      await api("/api/v1/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password, display_name: displayName }),
      });
    },
    async logout() {
      if (session?.refreshToken) {
        await api("/api/v1/auth/logout", {
          method: "POST",
          body: JSON.stringify({ refresh_token: session.refreshToken }),
        }).catch(() => undefined);
      }
      remember(null);
      setUser(null);
    },
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("AuthProvider is missing");
  }
  return value;
}
