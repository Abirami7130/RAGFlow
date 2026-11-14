import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "./auth/AuthProvider";
import { AppRoutes } from "./router";

const session = { accessToken: "access-token", refreshToken: "refresh-token" };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function installFetch(libraries: unknown[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/auth/me")) {
        return json({ id: "user-1", email: "ada@example.com", display_name: "Ada" });
      }
      if (url.includes("/knowledge-bases")) {
        return json({ items: libraries, next_cursor: null });
      }
      if (url.includes("/settings/providers")) {
        return json({
          embedding: { model: "BAAI/bge-small-en-v1.5", ready: false },
          reranker: { model: "cross-encoder/ms-marco-MiniLM-L-6-v2", ready: false },
          language_model: { provider: "gemini", model: "unset", ready: false },
        });
      }
      return json({ error: { code: "not_found", message: "Missing.", details: {} } }, 404);
    }),
  );
}

function renderAt(path: string, signedIn: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider initialSession={signedIn ? session : null}>
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes />
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("application shell", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends an anonymous visitor to sign in", async () => {
    installFetch();
    renderAt("/", false);
    expect(await screen.findByRole("heading", { name: "Sign in to the desk." })).toBeInTheDocument();
  });

  it("shows an empty knowledge-base index", async () => {
    installFetch();
    renderAt("/knowledge-bases", true);
    expect(await screen.findByRole("heading", { name: "Knowledge bases" })).toBeInTheDocument();
    expect(await screen.findByText("No knowledge bases yet. Create one to hold documents and questions.")).toBeInTheDocument();
  });

  it("navigates from the rail to documents and the playground", async () => {
    installFetch();
    renderAt("/", true);
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByText("No knowledge base yet. Create one to start a library.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Documents" }));
    expect(await screen.findByText("Nothing selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "RAG Playground" }));
    expect(await screen.findByRole("heading", { name: "RAG Playground" })).toBeInTheDocument();
    expect(screen.getByText("No run yet.")).toBeInTheDocument();
    expect(screen.getAllByText("waiting").length).toBeGreaterThan(0);
  });

  it("renders every primary route", async () => {
    installFetch();
    renderAt("/", true);
    expect(await screen.findByRole("link", { name: "Dashboard" })).toBeInTheDocument();
    const pages: Array<[string, RegExp]> = [
      ["Dashboard", /No knowledge base yet/],
      ["Knowledge Bases", /Knowledge bases/],
      ["Documents", /Nothing selected/],
      ["AI Chat", /No conversations yet/],
      ["Search", /Nothing retrieved/],
      ["RAG Playground", /No run yet/],
      ["Analytics", /No queries in this window/],
      ["Evaluations", /No evaluation sets yet/],
      ["Settings", /The Gemini key is read from the server environment/],
    ];
    for (const [link, marker] of pages) {
      fireEvent.click(screen.getByRole("link", { name: link }));
      expect(await screen.findByText(marker)).toBeInTheDocument();
    }
  });
});
