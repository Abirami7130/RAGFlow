import * as Dialog from "@radix-ui/react-dialog";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

const buttonBase =
  "inline-flex h-[34px] items-center justify-center px-3.5 font-heading text-[13px] transition-colors duration-150 disabled:opacity-50";

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "quiet" | "danger" }) {
  const styles = {
    primary: "bg-charcoal text-cream hover:bg-olive",
    secondary: "border border-line bg-transparent text-charcoal hover:bg-beige",
    quiet: "h-auto bg-transparent px-0 text-olive underline-offset-4 hover:underline",
    danger: "border border-dried-red bg-transparent text-dried-red hover:bg-beige",
  }[variant];
  return <button className={`${buttonBase} rounded-[2px] ${styles} ${className}`} {...props} />;
}

export function TextField({
  label,
  hint,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block font-body text-[12px] tracking-wide text-taupe">{label}</span>
      <input
        className="h-9 w-full rounded-[2px] border border-line bg-cream px-3 font-body text-[15px] text-charcoal placeholder:text-taupe"
        {...props}
      />
      {hint ? <span className="mt-1 block font-body text-[12px] text-taupe">{hint}</span> : null}
    </label>
  );
}

export function Sheet({
  open,
  onOpenChange,
  title,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-charcoal/10" />
        <Dialog.Content className="fixed top-0 right-0 flex h-full w-[400px] max-w-full flex-col border-l border-line bg-cream p-8">
          <Dialog.Title className="font-heading text-[22px] tracking-tight">{title}</Dialog.Title>
          <div className="mt-6 flex-1">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function FieldLabel({ children }: { children: ReactNode }) {
  return <span className="mb-1 block font-body text-[12px] tracking-wide text-taupe">{children}</span>;
}
