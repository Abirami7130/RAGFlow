import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useLibrary } from "../components/AppShell";
import { Button, TextField } from "../components/ui";
import { ApiError, api } from "../lib/api";
import { summarizeLocatorSpan } from "../lib/locator";
import type { ChunkPage, Document, ProcessingRun } from "../lib/types";

const RECENTS_KEY = "ragflow-recent-documents";
const PAGE_SIZE = 20;

type RecentDocument = { id: string; title: string; knowledge_base_id: string };

function storageAvailable(): boolean {
  return typeof localStorage !== "undefined" && typeof localStorage.getItem === "function";
}

function loadRecents(): RecentDocument[] {
  if (!storageAvailable()) {
    return [];
  }
  try {
    const raw = localStorage.getItem(RECENTS_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw) as RecentDocument[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function rememberRecent(doc: Document) {
  if (!storageAvailable()) {
    return;
  }
  const current = loadRecents().filter((item) => item.id !== doc.id);
  const next = [{ id: doc.id, title: doc.title, knowledge_base_id: doc.knowledge_base_id }, ...current].slice(
    0,
    12,
  );
  localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
}

function previewText(text: string, limit = 360): string {
  const trimmed = text.trim();
  if (trimmed.length <= limit) {
    return trimmed;
  }
  return `${trimmed.slice(0, limit).trimEnd()}…`;
}

function accessMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Could not load this document.";
  }
  if (error.status === 401) {
    return "Sign in again to view this document.";
  }
  if (error.status === 403) {
    return "You do not have access to this document.";
  }
  if (error.status === 404) {
    return "Document not found.";
  }
  return error.message;
}

export function DocumentsPage() {
  const { active, isLoading: librariesLoading, error: librariesError } = useLibrary();
  const [searchParams, setSearchParams] = useSearchParams();
  const [draftId, setDraftId] = useState(searchParams.get("document") ?? "");
  const selectedId = searchParams.get("document");

  const recents = useMemo(() => {
    if (!active) {
      return [];
    }
    return loadRecents().filter((item) => item.knowledge_base_id === active.id);
  }, [active, selectedId]);

  const documentQuery = useQuery({
    queryKey: ["document", selectedId],
    queryFn: () => api<Document>(`/api/v1/documents/${selectedId}`),
    enabled: Boolean(selectedId),
  });

  useEffect(() => {
    if (documentQuery.data) {
      rememberRecent(documentQuery.data);
    }
  }, [documentQuery.data]);

  const runOverride = searchParams.get("run");
  const runId = runOverride ?? documentQuery.data?.chunk_preview_run_id ?? null;

  const runQuery = useQuery({
    queryKey: ["processing-run", runId],
    queryFn: () => api<ProcessingRun>(`/api/v1/processing-runs/${runId}`),
    enabled: Boolean(runId),
  });

  const chunkQuery = useInfiniteQuery({
    queryKey: ["chunks", runId],
    queryFn: ({ pageParam }) => {
      const query = new URLSearchParams({ limit: String(PAGE_SIZE) });
      if (pageParam) {
        query.set("cursor", String(pageParam));
      }
      return api<ChunkPage>(`/api/v1/processing-runs/${runId}/chunks?${query.toString()}`);
    },
    enabled: Boolean(runId),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  });

  const chunks = chunkQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const versionLabel = useMemo(() => {
    const doc = documentQuery.data;
    if (!doc) {
      return "No version";
    }
    if (doc.preview_version_id && doc.pending_run_id) {
      return `Preview version · run ${runQuery.data?.status ?? doc.pending_status ?? "pending"}`;
    }
    if (doc.current_version_number !== null) {
      return `Version ${doc.current_version_number}`;
    }
    return "No version";
  }, [documentQuery.data, runQuery.data?.status]);

  function openDocument(id: string) {
    const trimmed = id.trim();
    if (!trimmed) {
      return;
    }
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("document", trimmed);
      next.delete("run");
      return next;
    });
  }

  return (
    <section>
      <h1 className="font-heading text-[22px] tracking-tight">Documents</h1>
      <div className="mt-6 grid min-h-[420px] grid-cols-1 border border-line lg:grid-cols-[38%_62%]">
        <div className="border-b border-line bg-cream lg:border-r lg:border-b-0">
          <div className="flex gap-4 border-b border-line px-4 py-3 font-heading text-[13px] text-taupe">
            <span>Upload</span>
            <span>URL</span>
            <span>GitHub</span>
          </div>
          <div className="px-4 py-6">
            {librariesLoading ? <p className="font-body text-[14px] text-taupe">Loading libraries.</p> : null}
            {librariesError ? <p className="font-body text-[14px] text-dried-red">{librariesError}</p> : null}
            {!librariesLoading && !librariesError && !active ? (
              <p className="mb-4 font-body text-[15px]">No knowledge base yet. Create one to hold documents.</p>
            ) : null}
            <p className="font-body text-[13px] text-taupe">
              Open a document by ID from ingest or the API. Listing uploads arrives in a later phase.
            </p>
            <form
              className="mt-4 flex flex-col gap-3"
              onSubmit={(event) => {
                event.preventDefault();
                openDocument(draftId);
              }}
            >
              <TextField
                id="document-id"
                label="Document ID"
                value={draftId}
                onChange={(event) => setDraftId(event.target.value)}
                placeholder="Paste a document UUID"
              />
              <Button type="submit">Open document</Button>
            </form>
            {active && recents.length ? (
              <ul className="mt-6 divide-y divide-line border-y border-line">
                {recents.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={`flex w-full flex-col gap-1 px-1 py-3 text-left hover:bg-beige ${
                        selectedId === item.id ? "bg-beige" : ""
                      }`}
                      onClick={() => {
                        setDraftId(item.id);
                        openDocument(item.id);
                      }}
                    >
                      <span className="font-display text-[20px] leading-tight">{item.title}</span>
                      <span className="font-mono text-[11px] text-taupe">{item.id}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-8 font-body text-[15px]">No documents yet. Open one by ID to preview chunks.</p>
            )}
          </div>
        </div>
        <div className="bg-ivory px-6 py-8">
          {!selectedId ? (
            <>
              <p className="font-display text-[32px] leading-none">Nothing selected</p>
              <p className="mt-4 font-mono text-[12.5px] text-taupe">No version · no chunks</p>
            </>
          ) : null}
          {selectedId && documentQuery.isLoading ? (
            <p className="font-body text-[15px] text-taupe">Loading document.</p>
          ) : null}
          {selectedId && documentQuery.isError ? (
            <p className="font-body text-[15px] text-dried-red">{accessMessage(documentQuery.error)}</p>
          ) : null}
          {documentQuery.data ? (
            <>
              <p className="font-display text-[32px] leading-none">{documentQuery.data.title}</p>
              <p className="mt-3 font-mono text-[12.5px] text-taupe">
                {versionLabel} · {documentQuery.data.chunk_count} chunks · {documentQuery.data.status}
              </p>
              {documentQuery.data.error_code ? (
                <p className="mt-3 font-body text-[14px] text-dried-red">
                  {documentQuery.data.error_code}
                  {documentQuery.data.error_detail ? ` — ${documentQuery.data.error_detail}` : ""}
                </p>
              ) : null}
              {!runId ? (
                <p className="mt-8 font-body text-[15px] text-taupe">No processing run to preview yet.</p>
              ) : null}
              {runId && runQuery.isLoading ? <p className="mt-6 font-body text-taupe">Loading run.</p> : null}
              {runId && runQuery.isError ? (
                <p className="mt-6 font-body text-dried-red">{accessMessage(runQuery.error)}</p>
              ) : null}
              {runQuery.data?.status === "failed" ? (
                <p className="mt-6 font-body text-dried-red">
                  Processing failed{runQuery.data.error_code ? ` (${runQuery.data.error_code})` : "."}
                </p>
              ) : null}
              {runId && chunkQuery.isLoading ? (
                <p className="mt-6 font-body text-taupe">Loading chunks.</p>
              ) : null}
              {runId && chunkQuery.isError ? (
                <p className="mt-6 font-body text-dried-red">{accessMessage(chunkQuery.error)}</p>
              ) : null}
              {runId && !chunkQuery.isLoading && !chunkQuery.isError && chunks.length === 0 ? (
                <p className="mt-8 font-body text-[15px] text-taupe">No chunks for this run.</p>
              ) : null}
              {chunks.length ? (
                <ol className="mt-8 divide-y divide-line border-y border-line">
                  {chunks.map((chunk) => (
                    <li key={chunk.id} className="py-5">
                      <ChunkMeta chunk={chunk} />
                      <p className="mt-2 whitespace-pre-wrap font-body text-[14px] leading-relaxed">
                        {previewText(chunk.text)}
                      </p>
                    </li>
                  ))}
                </ol>
              ) : null}
              {chunkQuery.hasNextPage ? (
                <div className="mt-6">
                  <Button
                    variant="quiet"
                    disabled={chunkQuery.isFetchingNextPage}
                    onClick={() => void chunkQuery.fetchNextPage()}
                  >
                    {chunkQuery.isFetchingNextPage ? "Loading…" : "Load more chunks"}
                  </Button>
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function ChunkMeta({
  chunk,
}: {
  chunk: {
    sequence: number;
    token_count: number;
    locator_start: Record<string, unknown>;
    locator_end: Record<string, unknown>;
  };
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-3 font-mono text-[12px] text-taupe">
      <span className="text-charcoal">#{chunk.sequence}</span>
      <span>{chunk.token_count} tokens</span>
      <span>{summarizeLocatorSpan(chunk.locator_start, chunk.locator_end)}</span>
    </div>
  );
}