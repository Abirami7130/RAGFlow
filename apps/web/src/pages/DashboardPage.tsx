import { Link } from "react-router-dom";
import { useLibrary } from "../components/AppShell";

export function DashboardPage() {
  const { active, libraries, isLoading, error } = useLibrary();

  if (isLoading) {
    return <p className="font-body text-taupe">Loading libraries.</p>;
  }
  if (error) {
    return <p className="font-body text-dried-red">{error}</p>;
  }
  if (!libraries.length || !active) {
    return (
      <section className="max-w-xl">
        <h1 className="font-display text-[40px] leading-none tracking-tight">Dashboard</h1>
        <p className="mt-4 font-body text-[15px]">No knowledge base yet. Create one to start a library.</p>
        <Link to="/knowledge-bases" className="mt-4 inline-block font-heading text-[14px] text-olive">
          Create a knowledge base
        </Link>
      </section>
    );
  }

  return (
    <section>
      <div className="flex items-end justify-between gap-6">
        <div>
          <h1 className="font-display text-[40px] leading-none tracking-tight">{active.name}</h1>
          <p className="mt-3 max-w-xl font-body text-[15px] text-charcoal">
            {active.description || "No description."}
          </p>
        </div>
        <div className="flex gap-5 font-heading text-[14px]">
          <Link to="/documents" className="text-olive">
            Add document
          </Link>
          <Link to="/chat" className="text-charcoal">
            Ask
          </Link>
        </div>
      </div>
      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-[1.4fr_1fr]">
        <section className="border border-line bg-cream">
          <header className="border-b border-line bg-beige px-5 py-3 font-heading text-[15px]">Library</header>
          <p className="px-5 py-8 font-body text-[15px] text-taupe">No documents yet.</p>
        </section>
        <section className="border border-line bg-cream">
          <header className="border-b border-line bg-beige px-5 py-3 font-heading text-[15px]">Latest inquiry</header>
          <div className="px-5 py-8">
            <p className="font-body text-[15px]">No questions yet.</p>
            <Link to="/chat" className="mt-3 inline-block font-heading text-[14px] text-olive">
              Open AI Chat
            </Link>
            <p className="mt-8 font-body text-[12px] text-taupe">Questions</p>
            <p className="mt-2 font-body text-[14px]">No queries in this window.</p>
          </div>
        </section>
      </div>
    </section>
  );
}
