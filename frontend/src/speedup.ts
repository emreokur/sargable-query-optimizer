export const speedup = (beforeMs: number, afterMs: number) => {
  const x = beforeMs / afterMs
  return x < 10 ? x.toFixed(1) : Math.round(x).toLocaleString('en-US')
}
