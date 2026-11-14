import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../lib/api";
import { useAuth } from "../auth/AuthProvider";
import { Button, TextField } from "../components/ui";

export function LoginPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  return (
    <AuthLayout title="Sign in to the desk." lede="Libraries, questions, and traces stay with your account.">
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          setPending(true);
          setError(null);
          void auth
            .login(String(data.get("email")), String(data.get("password")))
            .then(() => navigate("/"))
            .catch((reason: unknown) => setError(reason instanceof ApiError ? reason.message : "Sign in failed."))
            .finally(() => setPending(false));
        }}
      >
        <TextField label="Email" name="email" type="email" autoComplete="email" required />
        <TextField label="Password" name="password" type="password" autoComplete="current-password" required />
        {error ? <p className="font-body text-dried-red">{error}</p> : null}
        <Button type="submit" disabled={pending}>
          Sign in
        </Button>
        <Link to="/register" className="font-heading text-[13px] text-olive">
          Create an account
        </Link>
      </form>
    </AuthLayout>
  );
}

export function RegisterPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  return (
    <AuthLayout title="Open an account." lede="Registration does not sign you in.">
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          setPending(true);
          setError(null);
          void auth
            .register(String(data.get("email")), String(data.get("password")), String(data.get("display_name")))
            .then(() => navigate("/login"))
            .catch((reason: unknown) => setError(reason instanceof ApiError ? reason.message : "Registration failed."))
            .finally(() => setPending(false));
        }}
      >
        <TextField label="Name" name="display_name" required />
        <TextField label="Email" name="email" type="email" autoComplete="email" required />
        <TextField label="Password" name="password" type="password" minLength={10} hint="At least 10 characters." required />
        {error ? <p className="font-body text-dried-red">{error}</p> : null}
        <Button type="submit" disabled={pending}>
          Create account
        </Button>
        <Link to="/login" className="font-heading text-[13px] text-olive">
          Already registered
        </Link>
      </form>
    </AuthLayout>
  );
}

function AuthLayout({ title, lede, children }: { title: string; lede: string; children: ReactNode }) {
  return (
    <div className="min-h-screen bg-ivory px-8 py-16">
      <div className="mx-auto grid max-w-5xl items-start gap-16 md:grid-cols-2">
        <div>
          <p className="font-heading text-[18px]">RAGFlow</p>
          <h1 className="mt-8 font-display text-[40px] leading-none tracking-tight">{title}</h1>
          <p className="mt-4 max-w-sm font-body text-taupe">{lede}</p>
        </div>
        <div className="border border-line bg-cream p-8">{children}</div>
      </div>
    </div>
  );
}
