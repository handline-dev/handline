// Generated from contract/spec/ by contract/generate.py; do not edit.
/* eslint-disable */
import type { Accepted, RequestId, Role, Timestamp } from './core.ts';

export type Device = {
  id: string;
  name: string;
  /** A paired device is the founder. */
  role: "founder";
  created: Timestamp;
  last_seen: Timestamp | null;
  connected: boolean;
  /** Whether the device gave a push token (device.push_token) that the daemon still holds. */
  push: boolean;
};

export interface DevicesMethods {
  "devices.list": { params: { [key: string]: unknown }; result: {
    devices: Device[];
  } };
  "device.pair": { params: {
  name?: string;
  /** Seconds the code lives; default 300. */
  ttl_s?: number;
  request_id: RequestId;
}; result: Accepted & {
    code: string;
    expires_at: Timestamp;
    /** Listener certificate fingerprint. */
    fingerprint: string;
    /** What the QR carries. */
    uri: string;
  } };
  "device.redeem": { params: {
  code: string;
  name: string;
  request_id: RequestId;
}; result: Accepted & {
    token: string;
    device: string;
  } };
  "device.revoke": { params: {
  device: string;
  request_id: RequestId;
}; result: Accepted };
  "device.push_token": { params: {
  platform: "apns" | "fcm";
  token: string;
  /** An APNs token of a development build, which goes to Apple's sandbox host. Default false. */
  sandbox?: boolean;
  request_id: RequestId;
}; result: Accepted };
}

export const DEVICES_METHODS: { [K in keyof DevicesMethods]: { role: Role; callers: Role[] | null; write: boolean; since: number } } = {
  "devices.list": { role: "founder", callers: null, write: false, since: 1 },
  "device.pair": { role: "founder", callers: null, write: true, since: 1 },
  "device.redeem": { role: "anonymous", callers: null, write: true, since: 1 },
  "device.revoke": { role: "founder", callers: null, write: true, since: 1 },
  "device.push_token": { role: "founder", callers: null, write: true, since: 1 },
};
