const PATHS: Record<string, string> = {
  scale: "M12 4v16M5 8h14M7 8l-3 6h6zM17 8l-3 6h6z",
  search: "M11 5a6 6 0 1 0 0 12 6 6 0 0 0 0-12zM15.5 15.5 20 20",
  percent: "M6 18 18 6M8 6.5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0zM19 17.5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0z",
  receipt: "M6 3v18l2-1.4 2 1.4 2-1.4 2 1.4 2-1.4 2 1.4V3zM9 8h6M9 12h6M9 16h3",
  table: "M4 5h16v14H4zM4 10h16M10 10v9",
  ask: "M5 5h14v10H9l-4 4z",
};

export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name] ?? PATHS.scale} />
    </svg>
  );
}
