export default function BrandLogo({compact=false}){
  return <span className={`app-brand-logo ${compact?'compact':''}`} aria-label="Zotero Full Translate">
    <svg viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <path className="brand-sheet-back" d="M7 9.5A5.5 5.5 0 0 1 12.5 4h17A5.5 5.5 0 0 1 35 9.5v23A5.5 5.5 0 0 1 29.5 38h-17A5.5 5.5 0 0 1 7 32.5v-23Z"/>
      <path className="brand-sheet-front" d="M16 15.5a5.5 5.5 0 0 1 5.5-5.5h14a5.5 5.5 0 0 1 5.5 5.5v23a5.5 5.5 0 0 1-5.5 5.5h-14a5.5 5.5 0 0 1-5.5-5.5v-23Z"/>
      <path className="brand-line" d="M13 13h12M13 18h8M23 21h11M23 26h11M23 31h8"/>
      <path className="brand-arrow" d="M10.5 27.5h10m-3.5-3.5 3.5 3.5L17 31m20.5 4.5h-10m3.5-3.5-3.5 3.5L31 39"/>
    </svg>
  </span>
}
