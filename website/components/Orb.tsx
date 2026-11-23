export function Orb({ small }: { small?: boolean }) {
  return <span className={`orb ${small ? "small" : ""}`} aria-hidden="true"><i /></span>;
}
