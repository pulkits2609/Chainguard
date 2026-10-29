// Ledger viewers share the server's source allowance. Poll slowly and stop work
// in hidden tabs rather than spending that allowance while nobody can see it.
export const LedgerPollDelay = 10000;

export function retryAfterMilliseconds(value, now = Date.now()) {
  if (typeof value !== 'string' || !value.trim()) return 0;
  const text = value.trim();
  const milliseconds = /^\d+$/.test(text) ? Number(text) * 1000 : Date.parse(text) - now;
  return Number.isSafeInteger(milliseconds) && milliseconds > 0 ? milliseconds : 0;
}

export function startLedgerPolling({ read, onValue, onError, documentTarget = document,
  setTimer = setTimeout, clearTimer = clearTimeout, now = Date.now,
  delay = LedgerPollDelay, timeout = 5000 }) {
  let stopped = false;
  let timer;
  let current;
  let refreshPending = false;
  let retryNotBefore = 0;

  function cancelTimer() {
    if (timer !== undefined) clearTimer(timer);
    timer = undefined;
  }

  function schedule(wait) {
    cancelTimer();
    if (!stopped && !documentTarget.hidden) timer = setTimer(run, Math.max(wait, retryNotBefore - now(), 0));
  }

  async function run() {
    cancelTimer();
    if (stopped || documentTarget.hidden) return;
    if (current) { refreshPending = true; return; }
    if (now() < retryNotBefore) { schedule(retryNotBefore - now()); return; }
    refreshPending = false;
    const request = { controller: new AbortController(), hiddenAbort: false };
    current = request;
    request.deadline = setTimer(() => request.controller.abort(), timeout);
    try {
      const value = await read(request.controller.signal);
      if (!stopped && !documentTarget.hidden && !request.controller.signal.aborted) onValue(value);
    } catch (error) {
      if (!stopped && !request.hiddenAbort) {
        if (error.status === 429) retryNotBefore = now() + Math.max(delay, error.retryAfterMs || 0);
        if (!documentTarget.hidden) onError(error);
      }
    } finally {
      clearTimer(request.deadline);
      current = undefined;
      if (!stopped && !documentTarget.hidden) schedule(refreshPending ? 0 : delay);
    }
  }

  function refresh() {
    if (stopped) return;
    refreshPending = true;
    if (!documentTarget.hidden && !current) schedule(0);
  }

  function visibilityChanged() {
    if (documentTarget.hidden) {
      cancelTimer();
      if (current) {
        current.hiddenAbort = true;
        clearTimer(current.deadline);
        current.controller.abort();
      }
    } else refresh();
  }

  documentTarget.addEventListener('visibilitychange', visibilityChanged);
  void run();
  return {
    refresh,
    stop() {
      stopped = true;
      cancelTimer();
      current?.controller.abort();
      if (current) clearTimer(current.deadline);
      documentTarget.removeEventListener('visibilitychange', visibilityChanged);
    },
  };
}
