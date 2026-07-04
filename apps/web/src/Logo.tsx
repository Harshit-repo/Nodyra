export function Logo({ size = 26 }: { size?: number }) {
  const dimension = Math.max(0, size);

  return (
    <img
      className="nodyra-logo-mark"
      src="/brand/nodyra-mark.png"
      width={dimension}
      height={dimension}
      style={{ width: dimension, height: dimension }}
      alt=""
      aria-hidden="true"
      decoding="async"
      draggable={false}
    />
  );
}
