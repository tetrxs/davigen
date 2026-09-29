import { cn } from "@/lib/utils"

export function LogoMark({ className, size = 32 }: { className?: string; size?: number }) {
  return (
    <svg viewBox="0 0 64 64" className={cn("shrink-0", className)} style={{ width: size, height: size }} aria-hidden="true">
      <defs>
        <linearGradient id="davigen-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--brand)" />
          <stop offset="1" stopColor="var(--brand-2)" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" className="fill-sidebar-accent" />
      <circle cx="26" cy="32" r="13" fill="none" stroke="url(#davigen-mark)" strokeWidth="5.5" />
      <circle cx="39" cy="32" r="13" fill="none" stroke="url(#davigen-mark)" strokeWidth="5.5" opacity="0.55" />
    </svg>
  )
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("font-display text-[1.7rem] leading-none tracking-tight italic", className)}>
      davi<span className="text-brand-gradient">gen</span>
    </span>
  )
}
