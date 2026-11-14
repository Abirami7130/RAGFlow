import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useLibrary } from "../components/AppShell";
import { Button, TextField } from "../components/ui";
import { api } from "../lib/api";
import type { ChunkConfig, Providers, RetrievalConfig } from "../lib/types";

export function SettingsPage() {
  const auth = useAuth();
  const { active } = useLibrary();
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: () => api<Providers>("/api/v1/settings/providers"),
  });

  return (
    <section className="max-w-[640px]">
      <h1 className="font-heading text-[22px] tracking-tight">Settings</h1>
      <section className="mt-8 border-t border-line pt-6">
        <h2 className="font-heading text-[15px]">Profile</h2>
        <p className="mt-3 font-body">{auth.user?.display_name}</p>
        <p className="font-mono text-[12.5px] text-taupe">{auth.user?.email}</p>
      </section>
      <section className="mt-8 border-t border-line pt-6">
        <h2 className="font-heading text-[15px]">Language model</h2>
        {providers.isLoading ? <p className="mt-3 text-taupe">Checking providers.</p> : null}
        {providers.data ? (
          <ProviderLine
            label={providers.data.language_model.provider}
            model={providers.data.language_model.model}
            ready={providers.data.language_model.ready}
          />
        ) : null}
        <p className="mt-3 font-body text-[14px] text-taupe">
          The Gemini key is read from the server environment. This screen never asks for it.
        </p>
      </section>
      <section className="mt-8 border-t border-line pt-6">
        <h2 className="font-heading text-[15px]">Local models</h2>
        {providers.data ? (
          <div className="mt-3 space-y-2">
            <ProviderLine label="Embedding" model={providers.data.embedding.model} ready={providers.data.embedding.ready} />
            <ProviderLine label="Reranker" model={providers.data.reranker.model} ready={providers.data.reranker.ready} />
          </div>
        ) : null}
        <p className="mt-3 font-body text-[14px] text-taupe">Models stay unloaded until a later phase.</p>
      </section>
      <section className="mt-8 border-t border-line pt-6">
        <h2 className="font-heading text-[15px]">Defaults</h2>
        {active ? (
          <DefaultsForm key={active.id} id={active.id} retrieval={active.retrieval_config} chunk={active.chunk_config} canEdit={active.role !== "viewer"} />
        ) : (
          <p className="mt-3 text-taupe">Create a knowledge base before editing retrieval defaults.</p>
        )}
      </section>
    </section>
  );
}

function ProviderLine({ label, model, ready }: { label: string; model: string; ready: boolean }) {
  return (
    <p className="mt-3 font-body text-[14px]">
      <span className="font-heading">{label}</span>
      <span className="mx-2 font-mono text-[12.5px] text-taupe">{model}</span>
      <span className={ready ? "text-olive" : "text-taupe"}>{ready ? "Ready" : "Not ready"}</span>
    </p>
  );
}

function DefaultsForm({
  id,
  retrieval,
  chunk,
  canEdit,
}: {
  id: string;
  retrieval: RetrievalConfig;
  chunk: ChunkConfig;
  canEdit: boolean;
}) {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (body: { retrieval_config: RetrievalConfig; chunk_config: ChunkConfig }) =>
      api(`/api/v1/knowledge-bases/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
      setMessage("Saved. Reprocess is required after chunk changes, once ingestion exists.");
    },
  });

  return (
    <form
      className="mt-4 grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!canEdit) return;
        const data = new FormData(event.currentTarget);
        const number = (name: string, fallback: number) => Number(data.get(name) ?? fallback);
        save.mutate({
          retrieval_config: {
            ...retrieval,
            top_k_vector: number("top_k_vector", retrieval.top_k_vector),
            top_k_lexical: number("top_k_lexical", retrieval.top_k_lexical),
            context_k: number("context_k", retrieval.context_k),
            hybrid_enabled: data.get("hybrid_enabled") === "on",
            rerank_enabled: data.get("rerank_enabled") === "on",
          },
          chunk_config: {
            ...chunk,
            target_tokens: number("target_tokens", chunk.target_tokens),
            overlap_tokens: number("overlap_tokens", chunk.overlap_tokens),
          },
        });
      }}
    >
      <div className="grid grid-cols-2 gap-4">
        <TextField label="Vector k" name="top_k_vector" type="number" defaultValue={retrieval.top_k_vector} disabled={!canEdit} />
        <TextField label="Lexical k" name="top_k_lexical" type="number" defaultValue={retrieval.top_k_lexical} disabled={!canEdit} />
        <TextField label="Context k" name="context_k" type="number" defaultValue={retrieval.context_k} disabled={!canEdit} />
        <TextField label="Chunk tokens" name="target_tokens" type="number" defaultValue={chunk.target_tokens} disabled={!canEdit} />
        <TextField label="Overlap tokens" name="overlap_tokens" type="number" defaultValue={chunk.overlap_tokens} disabled={!canEdit} />
      </div>
      <label className="flex items-center gap-2 font-body text-[14px]">
        <input type="checkbox" name="hybrid_enabled" defaultChecked={retrieval.hybrid_enabled} disabled={!canEdit} />
        Hybrid search
      </label>
      <label className="flex items-center gap-2 font-body text-[14px]">
        <input type="checkbox" name="rerank_enabled" defaultChecked={retrieval.rerank_enabled} disabled={!canEdit} />
        Rerank
      </label>
      <p className="font-body text-[13px] text-taupe">
        Saving these defaults does not reindex anything. Ingestion is not part of this build.
      </p>
      {canEdit ? <Button type="submit">Save defaults</Button> : <p className="text-taupe">Viewers cannot edit defaults.</p>}
      {message ? <p className="text-olive">{message}</p> : null}
      {save.error instanceof Error ? <p className="text-dried-red">{save.error.message}</p> : null}
    </form>
  );
}
