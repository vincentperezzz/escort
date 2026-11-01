export function Button({ variant = "secondary", size = "md", className = "", ...props }) {
  const base =
    "press inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50";
  const sizes = { sm: "h-8 px-3 text-xs", md: "h-10 px-4 text-sm" };
  const variants = {
    primary: "bg-accent text-accent-ink hover:brightness-110",
    secondary: "border border-line-strong bg-raised text-ink hover:bg-sunken",
    ghost: "text-muted hover:bg-sunken hover:text-ink",
    danger: "bg-conflict text-white hover:brightness-110 dark:text-[#2a0a05]",
    dangerGhost: "border border-line-strong text-conflict hover:bg-conflict-soft",
  };
  return <button type="button" className={`${base} ${sizes[size]} ${variants[variant]} ${className}`} {...props} />;
}

export function Panel({ title, hint, action, children, className = "" }) {
  return (
    <section className={`rounded-xl border border-line bg-surface ${className}`}>
      {title || action ? (
        <header className="flex items-center justify-between gap-3 px-4 pt-4">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold">{title}</h3>
            {hint ? <p className="mt-0.5 text-xs text-muted">{hint}</p> : null}
          </div>
          {action}
        </header>
      ) : null}
      <div className="space-y-3 p-4">{children}</div>
    </section>
  );
}

export function PageHeader({ title, lede, children }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-xl">
        <h2 className="text-2xl font-semibold tracking-tight">{title}</h2>
        {lede ? <p className="mt-1.5 text-sm leading-relaxed text-muted">{lede}</p> : null}
      </div>
      {children}
    </div>
  );
}

export function Field({ label, hint, value, onChange, type = "text", placeholder = "", ...rest }) {
  return (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      <input
        className="mt-1.5 h-10 w-full rounded-lg border border-line-strong bg-raised px-3 text-sm placeholder:text-faint"
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        {...rest}
      />
      {hint ? <span className="mt-1 block text-xs text-muted">{hint}</span> : null}
    </label>
  );
}

export function Select({ label, value, onChange, options }) {
  return (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      <select
        className="mt-1.5 h-10 w-full rounded-lg border border-line-strong bg-raised px-3 text-sm"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Choose device</option>
        {options.map((device) => (
          <option key={device.id} value={device.id}>
            {device.name}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Chip({ children, onClick, tone = "neutral" }) {
  const tones = {
    neutral: "border-line-strong hover:bg-sunken",
    conflict: "border-conflict/40 text-conflict hover:bg-conflict-soft",
  };
  return (
    <button
      type="button"
      onClick={onClick}
      className={`press h-8 rounded-lg border bg-raised px-3 text-xs font-semibold transition-colors ${tones[tone]}`}
    >
      {children}
    </button>
  );
}

export function Empty({ title, body, children }) {
  return (
    <div className="rounded-xl border border-dashed border-line-strong px-6 py-10 text-center">
      <h3 className="text-base font-semibold">{title}</h3>
      <p className="mx-auto mt-1.5 max-w-sm text-sm leading-relaxed text-muted">{body}</p>
      {children ? <div className="mt-4 flex justify-center gap-2">{children}</div> : null}
    </div>
  );
}

export function Pill({ tone = "neutral", children }) {
  const tones = {
    neutral: "bg-sunken text-muted",
    good: "bg-commit-soft text-commit",
    warn: "bg-warn-soft text-warn",
    accent: "bg-accent-soft text-accent",
  };
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold ${tones[tone]}`}>{children}</span>
  );
}

export function WaveBars({ active, size = "md" }) {
  const dims = size === "sm" ? "h-4 gap-[2px]" : "h-6 gap-[3px]";
  return (
    <span className={`inline-flex items-end ${dims}`} aria-hidden>
      {[0, 1, 2, 3, 4].map((i) => (
        <span
          key={i}
          className={`level-bar block h-full w-[3px] rounded-full ${active ? "on bg-commit" : "bg-faint"}`}
          style={{ animationDelay: `${i * 130}ms` }}
        />
      ))}
    </span>
  );
}
