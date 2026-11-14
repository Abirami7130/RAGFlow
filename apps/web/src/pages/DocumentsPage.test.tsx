import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthProvider";
import { AppRoutes } from "../router";

const session = { accessToken: "access-token", refreshToken: "refresh-token" };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const library = {
  id: "kb-1",
  name: "Desk",
  description: "",
  role: "owner",
  document_count: 1,
  ready_count: 1,
  retrieval_config: {
    top_k_vector: 20,
    top_k_lexical: 20,
    rrf_k: 60,
    rerank_top_n: 10,
    context_k: 8,
    context_token_budget: 4000,
    hybrid_enabled: true,
    rerank_enabled: true,
    bm25_k1: 1.2,
    bm25_b: 0.75,
  },
  chunk_config: { strategy: "structure_aware", target_tokens: 256, overlap_tokens: 32, split_oversized_blocks: true },
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function installFetch(options: { documentError?: number } = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/auth/me")) {
        return json({ id: "user-1", email: "ada@example.com", display_name: "Ada" });
      }
      if (url.includes("/knowledge-bases") && !url.includes("/documents")) {
        return json({ items: [library], next_cursor: null });
      }
      if (url.includes("/documents/doc-1") && options.documentError) {
        return json({ error: { code: "not_found", message: "Document not found.", details: {} } }, options.documentError);
      }
      if (url.includes("/documents/doc-1")) {
        return json({
          id: "doc-1",
          knowledge_base_id: "kb-1",
          title: "Notes",
          source_type: "txt",
          source_uri: null,
          status: "ready",
          error_code: null,
          error_detail: null,
          current_version_id: "ver-1",
          current_version_number: 1,
          preview_version_id: null,
          pending_version_id: null,
          pending_run_id: null,
          pending_status: null,
          chunk_preview_run_id: "run-1",
          byte_size: 120,
          chunk_count: 2,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        });
      }
      if (url.includes("/processing-runs/run-1") && !url.includes("/chunks")) {
        return json({
          id: "run-1",
          document_version_id: "ver-1",
          status: "ready",
          is_active: true,
          chunk_count: 2,
          error_code: null,
        });
      }
      if (url.includes("/processing-runs/run-1/chunks")) {
        return json({
          items: [
            {
              id: "chunk-1",
              sequence: 0,
              text: "Alpha paragraph",
              token_count: 4,
              locator_start: { source_type: "txt", line: 1 },
              locator_end: { source_type: "txt", line: 1 },
              metadata: {},
            },
          ],
          next_cursor: null,
        });
      }
      return json({ error: { code: "not_found", message: "Missing.", details: {} } }, 404);
    }),
  );
}

function renderPage(path = "/documents") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider initialSession={session}>
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes />
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("DocumentsPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    if (typeof localStorage !== "undefined" && typeof localStorage.clear === "function") {
      localStorage.clear();
    }
  });

  it("shows the idle preview pane", async () => {
    installFetch();
    renderPage();
    expect(await screen.findByText("Nothing selected")).toBeInTheDocument();
    expect(await screen.findByPlaceholderText("Paste a document UUID")).toBeInTheDocument();
    expect(screen.getByText("No documents yet. Open one by ID to preview chunks.")).toBeInTheDocument();
  });

  it("loads chunk preview for a document id", async () => {
    installFetch();
    renderPage();
    const input = await screen.findByPlaceholderText("Paste a document UUID");
    fireEvent.change(input, { target: { value: "doc-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Open document" }));
    expect(await screen.findByText("Notes")).toBeInTheDocument();
    expect(await screen.findByText(/Version 1 · 2 chunks/)).toBeInTheDocument();
    expect(await screen.findByText("Alpha paragraph")).toBeInTheDocument();
    expect(screen.getByText(/4 tokens/)).toBeInTheDocument();
  });

  it("shows not found when the document is missing", async () => {
    installFetch({ documentError: 404 });
    renderPage("/documents?document=doc-1");
    expect(await screen.findByText("Document not found.")).toBeInTheDocument();
  });
});