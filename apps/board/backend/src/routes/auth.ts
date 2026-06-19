/**
 * Bearer-token middleware. Extracts the token from the Authorization
 * header (preferred) or the `token` query string (only used by SSE,
 * because EventSource doesn't let you set headers).
 *
 * On valid token, attaches { tokenId, tokenLabel } to res.locals.auth so
 * downstream handlers can log who did what.
 */

import type { NextFunction, Request, Response } from "express";

import { validateToken } from "../auth/tokens.js";

export interface AuthContext {
  readonly tokenId: number;
  readonly tokenLabel: string;
}

/**
 * Typed accessor for res.locals.auth. Avoids a global module
 * augmentation, which fights the existing Express typings.
 */
export function getAuthContext(
  res: { locals: Record<string, unknown> }
): AuthContext | undefined {
  const a = res.locals["auth"];
  if (
    a &&
    typeof a === "object" &&
    typeof (a as AuthContext).tokenId === "number" &&
    typeof (a as AuthContext).tokenLabel === "string"
  ) {
    return a as AuthContext;
  }
  return undefined;
}

function extractToken(req: Request): string | null {
  const header = req.header("authorization") ?? req.header("Authorization");
  if (header) {
    const m = /^Bearer\s+(.+)$/i.exec(header);
    if (m && m[1]) return m[1].trim();
  }
  const q = req.query["token"];
  if (typeof q === "string" && q.length > 0) return q;
  return null;
}

export function requireAuth(
  req: Request,
  res: Response,
  next: NextFunction
): void {
  const plaintext = extractToken(req);
  if (!plaintext) {
    res.status(401).json({ error: "missing bearer token" });
    return;
  }
  const row = validateToken(plaintext);
  if (!row) {
    res.status(401).json({ error: "invalid bearer token" });
    return;
  }
  res.locals["auth"] = { tokenId: row.id, tokenLabel: row.label };
  next();
}
