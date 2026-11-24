import { check } from 'k6';
import { WebSocket } from 'k6/websockets';
import { ENV } from '../config/env.js';
import {
  wsDeliveryDuration,
  wsForbiddenChannels,
  wsMessagesReceived,
  wsVersionRegressions,
} from './metrics.js';

const WS_READY_OPEN = WebSocket.OPEN ?? 1;

function parseFrame(rawData) {
  try {
    return JSON.parse(rawData);
  } catch {
    return null;
  }
}

function recordDeliveryLatency(msg) {
  if (!msg.occurredAt) {
    return;
  }
  const deltaMs = Date.now() - Date.parse(msg.occurredAt);
  if (!Number.isNaN(deltaMs) && deltaMs >= 0) {
    wsDeliveryDuration.add(deltaMs);
  }
}

function trackOrderVersion(msg, lastVersionByOrder) {
  if (!msg.orderId || typeof msg.version !== 'number') {
    return;
  }
  const prev = lastVersionByOrder.get(msg.orderId) ?? 0;
  if (msg.version < prev) {
    wsVersionRegressions.add(1);
    return;
  }
  lastVersionByOrder.set(msg.orderId, msg.version);
}

function processIncomingFrame(rawData, lastVersionByOrder) {
  wsMessagesReceived.add(1);
  const msg = parseFrame(rawData);
  if (!msg) {
    return;
  }
  if (msg.error === 'FORBIDDEN_CHANNEL') {
    wsForbiddenChannels.add(1);
    check(msg, { 'ws no forbidden channel': () => false });
    return;
  }
  recordDeliveryLatency(msg);
  trackOrderVersion(msg, lastVersionByOrder);
}

/**
 * Abre una conexion WebSocket con k6/websockets, suscribe canales y mide fan-out.
 */
export function openAndMonitorSocket(wsUrl, subprotocols, subscribeChannels) {
  const lastVersionByOrder = new Map();
  const ws = new WebSocket(wsUrl, subprotocols);

  let heartbeatId;
  let closeTimerId;

  const clearTimers = () => {
    if (heartbeatId !== undefined) {
      clearInterval(heartbeatId);
      heartbeatId = undefined;
    }
    if (closeTimerId !== undefined) {
      clearTimeout(closeTimerId);
      closeTimerId = undefined;
    }
  };

  ws.onopen = () => {
    for (const channel of subscribeChannels) {
      ws.send(JSON.stringify({ action: 'subscribe', channel }));
    }
    heartbeatId = setInterval(() => {
      if (ws.readyState === WS_READY_OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 15000);

    closeTimerId = setTimeout(() => {
      clearTimers();
      if (ws.readyState === WS_READY_OPEN) {
        ws.close();
      }
    }, ENV.wsHoldMs);
  };

  ws.onclose = clearTimers;
  ws.onerror = clearTimers;
  ws.onmessage = (event) => {
    processIncomingFrame(event.data, lastVersionByOrder);
  };
}
