import { Counter, Rate, Trend } from 'k6/metrics';

export const serverErrors5xx = new Rate('server_errors_5xx');
export const orderWriteDuration = new Trend('order_write_duration', true);
export const kdsReadDuration = new Trend('kds_read_duration', true);
export const wsDeliveryDuration = new Trend('ws_delivery_duration', true);
export const wsMessagesReceived = new Counter('ws_messages_received');
export const wsVersionRegressions = new Counter('ws_version_regressions');
export const wsForbiddenChannels = new Counter('ws_forbidden_channels');
export const stockRaceReserved = new Counter('stock_race_reserved');
export const stockRaceRejected = new Counter('stock_race_rejected');
export const stockRacePendingTimeout = new Counter('stock_race_pending_timeout');

export function trackServerError(res) {
  serverErrors5xx.add(res.status >= 500 || res.status === 0);
}
