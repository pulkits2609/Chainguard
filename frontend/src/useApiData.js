import { useEffect, useState } from 'react';
import { apiRequest } from './api.js';

export default function useApiData(path) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(Boolean(path));
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!path) { setData(null); setBusy(false); return undefined; }
    const controller = new AbortController();
    setBusy(true); setData(null); setError('');
    apiRequest(path, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setData(result);
    }).catch(requestError => {
      if (!controller.signal.aborted) setError(requestError.message);
    }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [path, revision]);
  return { data, busy, error, refresh: () => setRevision(value => value + 1) };
}
