export function cycleIndex(
  current: number,
  delta: number,
  count: number,
): number {
  return count > 0 ? (((current + delta) % count) + count) % count : 0;
}
export type WheelState = { sum: number; seen: number; fired: number };
export function wheelStep(
  delta: number,
  mode: number,
  now: number,
  state: WheelState,
): { delta: number; state: WheelState } {
  const amount = delta * (mode === 1 ? 16 : mode === 2 ? 800 : 1);
  const sum =
    (now - state.seen > 250 || Math.sign(amount) !== Math.sign(state.sum)
      ? 0
      : state.sum) + amount;
  const next = { sum, seen: now, fired: state.fired };
  if (now - state.fired < 180) return { delta: 0, state: { ...next, sum: 0 } };
  if (Math.abs(sum) < 40) return { delta: 0, state: next };
  return { delta: Math.sign(sum), state: { sum: 0, seen: now, fired: now } };
}
