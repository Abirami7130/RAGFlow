const stages = [
  "query_prepare",
  "embed_query",
  "vector_search",
  "lexical_search",
  "fuse",
  "rerank",
  "build_context",
  "build_prompt",
  "generate",
  "map_citations",
];

export function ChatPage() {
  return (
    <section className="grid min-h-[520px] grid-cols-1 border border-line lg:grid-cols-[240px_1fr_300px]">
      <aside className="border-b border-line bg-beige p-4 lg:border-r lg:border-b-0">
        <p className="font-heading text-[13px] text-taupe">Conversations</p>
        <p className="mt-4 font-body text-[14px]">No conversations yet.</p>
      </aside>
      <div className="flex flex-col bg-cream">
        <div className="flex-1 px-8 py-8">
          <p className="font-body text-[15px] text-taupe">Answers with citations arrive in a later phase.</p>
        </div>
        <form className="border-t border-line p-4" onSubmit={(event) => event.preventDefault()}>
          <label className="block font-body text-[12px] text-taupe" htmlFor="composer">
            Question
          </label>
          <input
            id="composer"
            disabled
            placeholder="Asking is not available yet"
            className="mt-1 h-9 w-full border border-line bg-ivory px-3 font-body"
          />
        </form>
      </div>
      <aside className="border-t border-line bg-ivory p-4 lg:border-t-0 lg:border-l">
        <p className="font-heading text-[13px] text-taupe">Citations</p>
        <p className="mt-4 font-body text-[14px]">No sources yet.</p>
      </aside>
    </section>
  );
}

export function SearchPage() {
  return (
    <section>
      <h1 className="font-heading text-[22px] tracking-tight">Search</h1>
      <form className="mt-6" onSubmit={(event) => event.preventDefault()}>
        <label className="font-body text-[12px] text-taupe" htmlFor="search">
          Query
        </label>
        <input
          id="search"
          disabled
          className="mt-1 w-full border-0 border-b border-line bg-transparent py-2 font-heading text-[22px]"
          placeholder="Retrieval is not available yet"
        />
      </form>
      <p className="mt-8 font-body">Nothing retrieved.</p>
    </section>
  );
}

export function PlaygroundPage() {
  return (
    <section>
      <h1 className="font-display text-[40px] leading-none tracking-tight">RAG Playground</h1>
      <p className="mt-3 max-w-2xl font-body text-taupe">
        Stages stay waiting until a real trace exists. This build does not run the pipeline.
      </p>
      <div className="mt-6 flex h-14 items-center gap-3 border border-line bg-beige px-4">
        <input
          disabled
          aria-label="Question"
          placeholder="No question"
          className="h-8 flex-1 bg-transparent font-body"
        />
        <span className="font-heading text-[13px] text-taupe">Run</span>
      </div>
      <div className="mt-4 flex gap-3 overflow-x-auto border border-line bg-ivory p-4">
        {stages.map((stage) => (
          <div key={stage} className="w-[160px] shrink-0 border border-line border-l-[3px] border-l-taupe bg-cream p-3">
            <p className="font-heading text-[13px]">{stage}</p>
            <p className="font-mono text-[12px] text-taupe">waiting</p>
          </div>
        ))}
      </div>
      <div className="mt-4 border border-line bg-cream p-5">
        <p className="font-heading text-[15px]">Inspector</p>
        <p className="mt-2 font-mono text-[12.5px] text-taupe">No run yet.</p>
      </div>
    </section>
  );
}

export function AnalyticsPage() {
  return (
    <section className="max-w-3xl">
      <h1 className="font-heading text-[22px] tracking-tight">Analytics</h1>
      <p className="mt-6 font-body">No queries in this window.</p>
      <p className="mt-2 font-body text-taupe">Counts appear after documents and questions exist.</p>
    </section>
  );
}

export function EvaluationsPage() {
  return (
    <section className="max-w-3xl">
      <h1 className="font-heading text-[22px] tracking-tight">Evaluations</h1>
      <p className="mt-6 font-body">No evaluation sets yet.</p>
      <p className="mt-2 font-body text-taupe">Experiment comparison arrives in a later phase.</p>
    </section>
  );
}
