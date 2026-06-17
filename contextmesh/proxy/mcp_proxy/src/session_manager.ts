import { CompressionClient } from "./compression_client.js";

interface SessionData {
  sessionId: string;
  taskDescription: string;
  recentSteps: string[];
  createdAt: Date;
}

class SessionManager {
  private sessions: Map<string, SessionData> = new Map();
  private currentSessionId: string | null = null;

  createSession(sessionId: string, taskDescription: string): SessionData {
    const session: SessionData = {
      sessionId,
      taskDescription,
      recentSteps: [],
      createdAt: new Date(),
    };
    this.sessions.set(sessionId, session);
    this.currentSessionId = sessionId;
    return session;
  }

  getSession(sessionId: string): SessionData | undefined {
    return this.sessions.get(sessionId);
  }

  getCurrentSession(): SessionData | undefined {
    if (this.currentSessionId) {
      return this.sessions.get(this.currentSessionId);
    }
    return undefined;
  }

  setCurrentSession(sessionId: string) {
    if (this.sessions.has(sessionId)) {
      this.currentSessionId = sessionId;
    }
  }

  addRecentStep(sessionId: string, step: string): void {
    const session = this.sessions.get(sessionId);
    if (session) {
      session.recentSteps.push(step);
      if (session.recentSteps.length > 3) {
        session.recentSteps.shift();
      }
    }
  }

  getTaskContext(sessionId?: string): { taskDescription: string; recentSteps: string[] } | null {
    const session = sessionId ? this.sessions.get(sessionId) : this.getCurrentSession();
    if (!session) return null;

    return {
      taskDescription: session.taskDescription,
      recentSteps: session.recentSteps,
    };
  }

  clearOldSessions(maxAgeMs: number = 3600000): number {
    const now = Date.now();
    let cleared = 0;

    for (const [id, session] of this.sessions.entries()) {
      if (now - session.createdAt.getTime() > maxAgeMs) {
        this.sessions.delete(id);
        cleared++;
      }
    }

    return cleared;
  }
}

export const sessionManager = new SessionManager();
