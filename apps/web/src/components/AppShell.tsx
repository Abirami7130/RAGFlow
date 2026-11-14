import { createContext, useContext, useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { api } from "../lib/api";
import type { KnowledgeBase } from "../lib/types";
import { Button } from "./ui";

const links = [
  ["/", "Dashboard"],
  ["/knowledge-bases", "Knowledge Bases"],
  ["/documents", "Documents"],
  ["/chat", "AI Chat"],
  ["/search", "Search"],
  ["/playground", "RAG Playground"],
  ["/analytics", "Analytics"],
  ["/evaluations", "Evaluations"],
  ["/settings", "Settings"],
] as const;

type LibraryValue = {
  libraries: KnowledgeBase[];
  active: KnowledgeBase | null;
  setActiveId: (id: string) => void;
  isLoading: boolean;
  error: string | null;
};

const LibraryContext = createContext<LibraryValue | null>(null);

export function useLibrary() {
  const value = useContext(LibraryContext);
  if (!value) {
    throw new Error("Library context is missing");
  }
  return value;
}

export function AppShell() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [activeId, setActiveId] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => api<{ items: KnowledgeBase[]; next_cursor: string | null }>("/api/v1/knowledge-bases"),
  });

  const libraries = query.data?.items ?? [];

  useEffect(() => {
    if (!libraries.length) {
      setActiveId(null);
      return;
    }
    if (!activeId || !libraries.some((item) => item.id === activeId)) {
      setActiveId(libraries[0].id);
    }
  }, [activeId, libraries]);

  const active = libraries.find((item) => item.id === activeId) ?? null;

  return (
    <LibraryContext.Provider
      value={{
        libraries,
        active,
        setActiveId,
        isLoading: query.isLoading,
        error: query.error instanceof Error ? query.error.message : null,
      }}
    >
      <div className="flex min-h-screen bg-ivory text-charcoal">
        <aside className="flex w-[216px] shrink-0 flex-col border-r border-line bg-beige">
          <div className="px-5 pt-6 font-heading text-[18px] tracking-tight">RAGFlow</div>
          <div className="px-5 pt-5">
            <label className="block font-body text-[12px] text-taupe" htmlFor="library-switcher">
              Library
            </label>
            {libraries.length ? (
              <select
                id="library-switcher"
                className="mt-1 w-full border-0 border-b border-line bg-transparent py-1 font-body text-[14px]"
                value={active?.id ?? ""}
                onChange={(event) => setActiveId(event.target.value)}
              >
                {libraries.map((library) => (
                  <option key={library.id} value={library.id}>
                    {library.name}
                  </option>
                ))}
              </select>
            ) : (
              <p className="mt-1 font-body text-[13px] text-taupe">No library yet</p>
            )}
          </div>
          <nav className="mt-6 flex flex-col" aria-label="Primary">
            {links.map(([to, label]) => (
              <NavLink
                key={to}
                to={to}
                end={to === "/"}
                className={({ isActive }) =>
                  `border-l-2 px-5 py-2 font-heading text-[14px] ${
                    isActive ? "border-olive text-olive" : "border-transparent text-charcoal hover:bg-ivory"
                  }`
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="mt-auto px-5 py-5">
            <Button
              variant="quiet"
              onClick={() => {
                void auth.logout().then(() => navigate("/login"));
              }}
            >
              Sign out
            </Button>
          </div>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-14 items-center justify-between border-b border-line px-8">
            <p className="truncate font-display text-[22px] leading-none">
              {active?.name ?? "RAGFlow"}
            </p>
            <p className="font-body text-[13px] text-taupe">{auth.user?.display_name ?? auth.user?.email}</p>
          </header>
          <main className="flex-1 px-8 py-8">
            <Outlet />
          </main>
        </div>
      </div>
    </LibraryContext.Provider>
  );
}
