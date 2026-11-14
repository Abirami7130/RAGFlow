import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useLibrary } from "../components/AppShell";
import { Button, Sheet, TextField } from "../components/ui";
import { ApiError, api } from "../lib/api";
import type { KnowledgeBase } from "../lib/types";

export function KnowledgeBasesPage() {
  const { libraries, isLoading, error, setActiveId } = useLibrary();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<KnowledgeBase | null>(null);
  const queryClient = useQueryClient();

  const create = useMutation({
    mutationFn: (body: { name: string; description: string }) =>
      api<KnowledgeBase>("/api/v1/knowledge-bases", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
      setActiveId(created.id);
      setOpen(false);
    },
  });

  const save = useMutation({
    mutationFn: (body: { id: string; name: string; description: string }) =>
      api<KnowledgeBase>(`/api/v1/knowledge-bases/${body.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name: body.name, description: body.description }),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
      setSelected(null);
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/v1/knowledge-bases/${id}`, { method: "DELETE" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
      setSelected(null);
    },
  });

  return (
    <section>
      <div className="flex items-end justify-between">
        <h1 className="font-display text-[40px] leading-none tracking-tight">Knowledge bases</h1>
        <Button onClick={() => setOpen(true)}>New knowledge base</Button>
      </div>
      {isLoading ? <p className="mt-8 text-taupe">Loading libraries.</p> : null}
      {error ? <p className="mt-8 text-dried-red">{error}</p> : null}
      {!isLoading && !error && libraries.length === 0 ? (
        <p className="mt-8 max-w-lg font-body">No knowledge bases yet. Create one to hold documents and questions.</p>
      ) : null}
      <ul className="mt-8 divide-y divide-line border-y border-line">
        {libraries.map((library) => (
          <li key={library.id}>
            <button
              className="flex w-full items-baseline justify-between gap-6 px-1 py-5 text-left hover:bg-beige"
              onClick={() => {
                setActiveId(library.id);
                setSelected(library);
              }}
            >
              <span>
                <span className="block font-display text-[28px] leading-none">{library.name}</span>
                <span className="mt-2 block font-body text-[14px] text-taupe">
                  {library.description || "No description."}
                </span>
              </span>
              <span className="shrink-0 font-mono text-[12.5px] text-taupe">
                {library.document_count} documents · {library.ready_count} ready
              </span>
            </button>
          </li>
        ))}
      </ul>
      <CreateSheet
        open={open}
        error={create.error instanceof ApiError ? create.error.message : null}
        pending={create.isPending}
        onOpenChange={setOpen}
        onCreate={(name, description) => create.mutate({ name, description })}
      />
      <EditSheet
        library={selected}
        error={
          save.error instanceof ApiError
            ? save.error.message
            : remove.error instanceof ApiError
              ? remove.error.message
              : null
        }
        onOpenChange={(next) => {
          if (!next) setSelected(null);
        }}
        onSave={(name, description) => {
          if (selected) save.mutate({ id: selected.id, name, description });
        }}
        onDelete={(id) => remove.mutate(id)}
      />
    </section>
  );
}

function CreateSheet({
  open,
  onOpenChange,
  onCreate,
  pending,
  error,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (name: string, description: string) => void;
  pending: boolean;
  error: string | null;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="New knowledge base">
      <form
        className="flex h-full flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          onCreate(String(data.get("name") ?? ""), String(data.get("description") ?? ""));
        }}
      >
        <TextField label="Name" name="name" required placeholder="Research" />
        <TextField label="Description" name="description" placeholder="Optional" />
        {error ? <p className="text-dried-red">{error}</p> : null}
        <div className="mt-auto">
          <Button type="submit" disabled={pending}>
            Create
          </Button>
        </div>
      </form>
    </Sheet>
  );
}

function EditSheet({
  library,
  onOpenChange,
  onSave,
  onDelete,
  error,
}: {
  library: KnowledgeBase | null;
  onOpenChange: (open: boolean) => void;
  onSave: (name: string, description: string) => void;
  onDelete: (id: string) => void;
  error: string | null;
}) {
  const [confirmation, setConfirmation] = useState("");
  if (!library) {
    return null;
  }
  return (
    <Sheet open={library !== null} onOpenChange={onOpenChange} title={library.name}>
      <form
        key={library.id}
        className="flex h-full flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          onSave(String(data.get("name") ?? ""), String(data.get("description") ?? ""));
        }}
      >
        <TextField label="Name" name="name" required defaultValue={library.name} />
        <TextField label="Description" name="description" defaultValue={library.description} />
        <p className="font-mono text-[12.5px] text-taupe">Role {library.role}</p>
        {error ? <p className="text-dried-red">{error}</p> : null}
        <Button type="submit">Save</Button>
        {library.role === "owner" ? (
          <div className="mt-8 border-t border-line pt-6">
            <p className="font-body text-[13px] text-taupe">Type the library name to delete it.</p>
            <TextField
              label="Confirm name"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
            />
            <Button
              type="button"
              variant="danger"
              className="mt-3"
              disabled={confirmation !== library.name}
              onClick={() => onDelete(library.id)}
            >
              Delete knowledge base
            </Button>
          </div>
        ) : null}
      </form>
    </Sheet>
  );
}
