import { randomUUID } from "crypto";

interface SessionData {
  sessionId: string;
  taskDescription: string;
  recentSteps: string[];
  /** Cumulative compressed tokens returned to the agent this session. */
  compressedTokensUsed: number;
  createdAt: Date;
  lastAccessedAt: Date;
}

interface TaskContext {
  taskDescription: string;
  recentSteps: string[];
}

export class SessionManager {
  private sessions: Map<string, SessionData> = new Map();
  private currentSessionId: string | null = null;
  private sessionTimeoutMs: number;

  constructor(sessionTimeoutMinutes: number = 60) {
    this.sessionTimeoutMs = sessionTimeoutMinutes * 60 * 1000;
  }

  createSession(taskDescription: string): string {
    const sessionId = randomUUID();
    const now = new Date();

    const session: SessionData = {
      sessionId,
      taskDescription,
      recentSteps: [],
      compressedTokensUsed: 0,
      createdAt: now,
      lastAccessedAt: now,
    };

    this.sessions.set(sessionId, session);
    this.currentSessionId = sessionId;
    return sessionId;
  }

  getOrCreateSession(taskDescription: string): string {
    if (this.currentSessionId && this.sessions.has(this.currentSessionId)) {
      const session = this.sessions.get(this.currentSessionId)!;
      session.lastAccessedAt = new Date();
      return session.sessionId;
    }

    return this.createSession(taskDescription);
  }

  getSession(sessionId: string): SessionData | undefined {
    const session = this.sessions.get(sessionId);
    if (session) {
      session.lastAccessedAt = new Date();
    }
    return session;
  }

  getCurrentSession(): SessionData | undefined {
    if (this.currentSessionId) {
      return this.getSession(this.currentSessionId);
    }
    return undefined;
  }

  setCurrentSession(sessionId: string): boolean {
    if (this.sessions.has(sessionId)) {
      this.currentSessionId = sessionId;
      return true;
    }
    return false;
  }

  addRecentStep(sessionId: string, step: string): void {
    const session = this.sessions.get(sessionId);
    if (session) {
      session.recentSteps.push(step);
      if (session.recentSteps.length > 3) {
        session.recentSteps.shift();
      }
      session.lastAccessedAt = new Date();
    }
  }

  addCompressedTokens(sessionId: string, tokens: number): void {
    const session = this.sessions.get(sessionId);
    if (session && Number.isFinite(tokens) && tokens > 0) {
      session.compressedTokensUsed += tokens;
      session.lastAccessedAt = new Date();
    }
  }

  getCompressedTokensUsed(sessionId: string): number {
    return this.sessions.get(sessionId)?.compressedTokensUsed ?? 0;
  }

  getTaskContext(sessionId?: string): TaskContext | null {
    const session = sessionId
      ? this.sessions.get(sessionId)
      : this.getCurrentSession();

    if (!session) return null;

    return {
      taskDescription: session.taskDescription,
      recentSteps: [...session.recentSteps],
    };
  }

  clearOldSessions(): number {
    const now = Date.now();
    let cleared = 0;

    for (const [id, session] of this.sessions.entries()) {
      if (now - session.lastAccessedAt.getTime() > this.sessionTimeoutMs) {
        this.sessions.delete(id);
        cleared++;
      }
    }

    return cleared;
  }

  getSessionCount(): number {
    return this.sessions.size;
  }
}
