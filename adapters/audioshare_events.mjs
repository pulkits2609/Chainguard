// Application-side Node preload. Configuration is evaluated before the source entry point.
import { EventForwarder } from './event_forwarder.mjs';
import { installAudioShareEvents } from './audioshare_observer.mjs';

if (process.env.CHAINGUARD_EVENT_URL || process.env.CHAINGUARD_EVENT_TOKEN) {
  const forwarder = new EventForwarder({ endpoint: process.env.CHAINGUARD_EVENT_URL,
    token: process.env.CHAINGUARD_EVENT_TOKEN,
    allowLoopbackHttp: process.env.CHAINGUARD_ADAPTER_ALLOW_LOOPBACK_HTTP === 'true',
    onFailure: ({ reason, status, attempts }) => {
      console.error(`ChainGuard adapter: ${reason}; status=${status ?? 'unavailable'}; attempts=${attempts}`);
    } });
  installAudioShareEvents(forwarder);
  console.info('ChainGuard application-side operational event adapter active');
}
