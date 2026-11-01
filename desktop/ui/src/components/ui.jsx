const BUTTON_SIZES = {
  sm: "h-8 pl-3.5 pr-3.5 text-xs gap-1.5",
  md: "h-10 pl-5 pr-5 text-sm gap-2",
  lg: "h-14 pl-7 pr-7 text-[15px] gap-3",
};
const TRAIL_SIZES = { sm: "-mr-2 h-6 w-6", md: "-mr-3.5 h-8 w-8", lg: "-mr-5 h-10 w-10" };
const BUTTON_VARIANTS = {
  primary: "bg-ink text-bg hover:bg-white",
  accent: "bg-accent text-accent-ink hover:bg-[#9a8dff]",
  secondary: "bg-white/[0.06] text-ink hairline-strong hover:bg-white/[0.1]",
  ghost: "text-muted hover:bg-white/[0.06] hover:text-ink",
  danger: "bg-conflict text-[#1d0703] hover:bg-[#ff7d6b]",
  dangerGhost: "text-conflict hairline-strong hover:bg-conflict-soft",
};
const TRAIL_TONES = {
  primary: "bg-bg/10",
  accent: "bg-black/15",
  secondary: "bg-white/[0.08]",
  ghost: "bg-white/[0.06]",
  danger: "bg-black/15",
  dangerGhost: "bg-conflict-soft",
};

/** Pill button. `trailing` nests an icon in its own circle (button-in-button). */
export function Button({ variant = "secondary", size = "md", trailing = null, className = "", children, ...props }) {
  return (
    <button
      type="button"
      className={`press group inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-full font-medium tracking-[-0.005em] disabled:cursor-not-allowed disabled:opacity-45 ${BUTTON_SIZES[size]} ${BUTTON_VARIANTS[variant]} ${className}`}
      {...props}
    >
      {children}
      {trailing ? (
        <span className={`btn-icon grid shrink-0 place-items-center rounded-full ${TRAIL_SIZES[size]} ${TRAIL_TONES[variant]}`} aria-hidden>
          {trailing}
        </span>
      ) : null}
    </button>
  );
}

/** Circular icon-only button. */
export function IconButton({ label, variant = "ghost", size = 36, className = "", children, ...props }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      style={{ width: size, height: size }}
      className={`press grid shrink-0 place-items-center rounded-full disabled:cursor-not-allowed disabled:opacity-45 ${BUTTON_VARIANTS[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

/** Double-bezel enclosure: outer shell (tray) + inner core (plate), concentric radii. */
export function Bezel({ as: Tag = "section", size = "md", className = "", coreClassName = "", children, ...rest }) {
  const sizes = { md: "", sm: "bezel-sm", xs: "bezel-xs" };
  return (
    <Tag className={`bezel ${sizes[size]} ${className}`} {...rest}>
      <div className={`bezel-core h-full ${coreClassName}`}>{children}</div>
    </Tag>
  );
}

export function Panel({ title, hint, action, children, className = "", size = "sm" }) {
  return (
    <Bezel size={size} className={className}>
      {title || action ? (
        <header className="flex items-start justify-between gap-3 px-5 pt-5">
          <div className="min-w-0">
            <h3 className="text-[15px] font-medium tracking-[-0.01em]">{title}</h3>
            {hint ? <p className="mt-1 text-[13px] leading-relaxed text-muted">{hint}</p> : null}
          </div>
          {action}
        </header>
      ) : null}
      <div className="space-y-4 p-5">{children}</div>
    </Bezel>
  );
}

export function PageHeader({ title, lede, children }) {
  return (
    <div className="mb-10 flex flex-wrap items-end justify-between gap-5">
      <div className="max-w-xl">
        <h1 className="text-[34px] font-semibold leading-[1.05] tracking-[-0.035em] text-balance">{title}</h1>
        {lede ? <p className="mt-3 text-[15px] leading-relaxed text-muted text-pretty">{lede}</p> : null}
      </div>
      {children}
    </div>
  );
}

export function Field({ label, hint, value, onChange, type = "text", placeholder = "", ...rest }) {
  return (
    <label className="block">
      <span className="text-[13px] font-medium text-ink/90">{label}</span>
      <input
        className="field mt-2 h-11 w-full px-4 text-sm"
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        {...rest}
      />
      {hint ? <span className="mt-1.5 block text-xs text-muted">{hint}</span> : null}
    </label>
  );
}

export function Select({ label, value, onChange, options, placeholder = "Choose device" }) {
  return (
    <label className="block">
      <span className="text-[13px] font-medium text-ink/90">{label}</span>
      <select className="field mt-2 h-11 w-full px-3.5 text-sm" value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">{placeholder}</option>
        {options.map((device) => (
          <option key={device.id} value={device.id}>
            {device.name}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Chip({ children, onClick, tone = "neutral", className = "", ...rest }) {
  const tones = {
    neutral: "bg-white/[0.05] text-ink/90 hairline hover:bg-white/[0.09] hover:text-ink",
    accent: "bg-accent-soft text-[#c6bfff] hover:bg-[rgba(139,124,255,0.22)]",
    conflict: "bg-conflict-soft text-[#ffb3a7] hover:bg-[rgba(255,106,85,0.2)]",
  };
  return (
    <button
      type="button"
      onClick={onClick}
      className={`press inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full px-3.5 text-[12.5px] font-medium disabled:opacity-45 ${tones[tone]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Empty({ title, body, children, className = "" }) {
  return (
    <Bezel size="sm" className={className} coreClassName="px-8 py-12 text-center">
      <h3 className="text-[17px] font-medium tracking-[-0.015em]">{title}</h3>
      <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-muted text-pretty">{body}</p>
      {children ? <div className="mt-6 flex justify-center gap-2">{children}</div> : null}
    </Bezel>
  );
}

export function Pill({ tone = "neutral", children, className = "" }) {
  const tones = {
    neutral: "bg-white/[0.06] text-muted",
    good: "bg-commit-soft text-commit",
    warn: "bg-warn-soft text-warn",
    accent: "bg-accent-soft text-[#c6bfff]",
    conflict: "bg-conflict-soft text-conflict",
  };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11.5px] font-medium leading-none ${tones[tone]} ${className}`}>
      {children}
    </span>
  );
}

/** Accessible on/off switch. */
export function Toggle({ checked, onChange, label, disabled = false, className = "" }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-[26px] w-[44px] shrink-0 items-center rounded-full p-[3px] transition-colors duration-300 ease-glass disabled:cursor-not-allowed disabled:opacity-45 ${
        checked ? "bg-accent" : "bg-white/[0.1] hairline"
      } ${className}`}
    >
      <span
        className={`block h-5 w-5 rounded-full shadow-[0_2px_6px_rgba(0,0,0,0.35)] transition-transform duration-500 ease-glass ${
          checked ? "translate-x-[18px] bg-white" : "translate-x-0 bg-[#c9ccd8]"
        }`}
      />
    </button>
  );
}

/** Small status dot; `live` adds a soft pulse ring. */
export function Dot({ tone = "faint", live = false, className = "" }) {
  const tones = { faint: "text-faint", signal: "text-conflict", mint: "text-commit", iris: "text-accent" };
  return (
    <span className={`relative inline-block h-2 w-2 shrink-0 rounded-full bg-current ${live ? "dot-live" : ""} ${tones[tone]} ${className}`} aria-hidden />
  );
}

export function Kbd({ children }) {
  return (
    <kbd className="mono inline-flex h-[22px] min-w-[22px] items-center justify-center rounded-md bg-white/[0.06] px-1.5 text-[11px] font-medium text-muted hairline">
      {children}
    </kbd>
  );
}

export function WaveBars({ active, size = "md" }) {
  const dims = size === "sm" ? "h-4 gap-[2px]" : "h-6 gap-[3px]";
  return (
    <span className={`inline-flex items-end ${dims}`} aria-hidden>
      {[0, 1, 2, 3, 4].map((i) => (
        <span
          key={i}
          className={`level-bar block h-full w-[3px] rounded-full ${active ? "on bg-conflict" : "bg-faint/70"}`}
          style={{ animationDelay: `${i * 130}ms` }}
        />
      ))}
    </span>
  );
}
