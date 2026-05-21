export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M5 21 C 9 9, 13.5 9, 16 16 S 23 23, 27 11"
        stroke="var(--accent)"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <circle cx="5" cy="21" r="3.6" fill="var(--accent)" />
      <circle cx="27" cy="11" r="3.6" fill="var(--accent-2)" />
    </svg>
  );
}
