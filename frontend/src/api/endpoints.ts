import { request, newId } from './client'
import type {
  CaseView,
  ConversationView,
  Language,
  LoginRequest,
  MessageRequest,
  OperationView,
  ReceiptView,
  SessionView,
  TurnResult,
  VoiceSessionGrant,
} from './types'

// Thin typed wrappers over contracts/openapi.json (Resolve). Business rules live in Resolve, not here.

export const customerApi = {
  getSession: () => request<SessionView>('customer', 'GET', '/session'),
  /** Voice needs an account-scoped CUSTOMER session; a guest has no line to look into. */
  login: (body: LoginRequest) => request<SessionView>('customer', 'POST', '/demo/sessions', { body }),
  logout: () => request<void>('customer', 'DELETE', '/session'),

  createConversation: (language: Language, idempotencyKey = newId()) =>
    request<ConversationView>('customer', 'POST', '/conversations', { body: { language }, idempotencyKey }),
  getConversation: (id: string) => request<ConversationView>('customer', 'GET', `/conversations/${id}`),
  /** Text continuation of the same conversation. Reuse body.client_turn_id when retrying the same turn. */
  sendMessage: (conversationId: string, body: MessageRequest) =>
    request<TurnResult>('customer', 'POST', `/conversations/${conversationId}/messages`, { body }),

  /**
   * Resolve stores a scoped binding, asks Voice for a single-use browser grant and returns it with the
   * WebSocket URL. The grant is good for about 60 seconds and one connection; request a new one per call.
   */
  createVoiceSession: (conversationId: string, idempotencyKey = newId()) =>
    request<VoiceSessionGrant>('customer', 'POST', `/conversations/${conversationId}/voice-sessions`, {
      body: {},
      idempotencyKey,
    }),

  getCase: (id: string) => request<CaseView>('customer', 'GET', `/cases/${id}`),
  getOperation: (id: string, signal?: AbortSignal) =>
    request<OperationView>('customer', 'GET', `/operations/${id}`, { signal }),
  getReceipt: (caseId: string) => request<ReceiptView>('customer', 'GET', `/cases/${caseId}/receipt`),
}
