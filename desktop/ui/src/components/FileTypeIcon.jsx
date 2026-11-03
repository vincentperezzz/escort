/**
 * Google Drive's file-type icons: a coloured page with a folded corner and the type's glyph
 * (Docs lines, Sheets grid, Slides frame, PDF label), so a file reads at a glance like in Drive.
 */

const GOOGLE_DOC = "application/vnd.google-apps.document";
const GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet";
const GOOGLE_SLIDES = "application/vnd.google-apps.presentation";
const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

const PAGE = "M5 1.5h9.5L20 7v14a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 21V3A1.5 1.5 0 0 1 5.5 1.5z";
const FOLD = "M14.5 1.5V5.5A1.5 1.5 0 0 0 16 7h4z";

function Page({ size, color, fold, label, children }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} role="img" aria-label={label}>
      <path d={PAGE} fill={color} />
      <path d={FOLD} fill={fold} />
      {children}
    </svg>
  );
}

function Docs({ size }) {
  return (
    <Page size={size} color="#4285F4" fold="#A1C2FA" label="Google Doc">
      <path d="M7.5 11h9M7.5 13.75h9M7.5 16.5h9M7.5 19.25h6" stroke="#fff" strokeWidth="1.3" strokeLinecap="round" />
    </Page>
  );
}

function Sheets({ size }) {
  return (
    <Page size={size} color="#0F9D58" fold="#87CEAC" label="Google Sheet">
      <rect x="7" y="10.5" width="10" height="9" rx="0.6" fill="none" stroke="#fff" strokeWidth="1.3" />
      <path d="M7 13.5h10M7 16.5h10M11 10.5v9" stroke="#fff" strokeWidth="1.3" />
    </Page>
  );
}

function Slides({ size }) {
  return (
    <Page size={size} color="#F4B400" fold="#FADA80" label="Google Slides">
      <rect x="7" y="11.5" width="10" height="6.5" rx="0.6" fill="none" stroke="#fff" strokeWidth="1.4" />
    </Page>
  );
}

function Pdf({ size }) {
  return (
    <Page size={size} color="#EA4335" fold="#F5A29A" label="PDF">
      <text x="12" y="17.6" textAnchor="middle" fontSize="5.6" fontWeight="700" fontFamily="Arial, sans-serif" fill="#fff">
        PDF
      </text>
    </Page>
  );
}

function Word({ size }) {
  return (
    <Page size={size} color="#2B579A" fold="#95ABCD" label="Word document">
      <text x="12" y="18.2" textAnchor="middle" fontSize="8" fontWeight="700" fontFamily="Arial, sans-serif" fill="#fff">
        W
      </text>
    </Page>
  );
}

function Text({ size }) {
  return (
    <Page size={size} color="#5F6368" fold="#AFB1B3" label="Text file">
      <path d="M7.5 11h9M7.5 13.75h9M7.5 16.5h6" stroke="#fff" strokeWidth="1.3" strokeLinecap="round" />
    </Page>
  );
}

const BY_MIME = {
  [GOOGLE_DOC]: Docs,
  [GOOGLE_SHEET]: Sheets,
  [GOOGLE_SLIDES]: Slides,
  "application/pdf": Pdf,
  [DOCX]: Word,
};

export default function FileTypeIcon({ mime = "", size = 24, className = "" }) {
  const Icon = BY_MIME[mime] || Text;
  return (
    <span className={`inline-grid shrink-0 place-items-center ${className}`} style={{ width: size, height: size }}>
      <Icon size={size} />
    </span>
  );
}
