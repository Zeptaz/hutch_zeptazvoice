// Mock Resolve for VITE_API_MODE=mock. Shapes come from contracts/examples.json; state lives in
// sessionStorage so a refresh keeps the conversation. Decisions here are scripted demo behaviour,
// not business rules: in live mode Resolve owns investigation, proposals and confirmation.
import examplesFile from '@contracts/examples.json'
import type { RawResponse, Realm, RequestOptions } from './client'
import type {
  Card,
  ConversationView,
  MessageRequest,
  MessageView,
  OperationView,
  ProposalView,
  SessionView,
  TurnResult,
  VoiceProposal,
  VoiceSessionGrant,
} from './types'

type Examples = Record<string, { value: unknown }>
const examples = (examplesFile as unknown as { examples: Examples }).examples
const example = <T>(name: string): T => structuredClone(examples[name].value) as T

const KEY = 'hutch-voice.mock'
const LATENCY_MS = 350
const OPERATION_MS = 3000

type Store = {
  session: SessionView | null
  expired: boolean
  outage: boolean
  conversations: Record<string, ConversationView>
  operations: Record<string, { op: OperationView; createdAt: number }>
}

function load(): Store {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (raw) return JSON.parse(raw) as Store
  } catch {
    /* fall through to a fresh store */
  }
  return { session: null, expired: false, outage: false, conversations: {}, operations: {} }
}
function save(s: Store) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(s))
  } catch {
    /* mock state just won't survive a refresh */
  }
}

const uuid = () => crypto.randomUUID()
const nowIso = () => new Date().toISOString()
const plus = (ms: number) => new Date(Date.now() + ms).toISOString()
const ok = (status: number, body: unknown): RawResponse => ({ status, body })
const fail = (status: number, code: string, message: string, retryable = false): RawResponse => ({
  status,
  body: { error: { code, message, retryable, request_id: uuid(), details: {} } },
})

// ---- Scripted replies, shared by text turns and the mock Voice socket -------------------------

export type MockReply = {
  reply_text: string
  speech_text: string
  case_id: string | null
  proposal: VoiceProposal | null
  operation_status: string | null
  end_session: boolean
}

const YES = /\b(yes|yeah|yep|ok(ay)?|sure|go ahead|do it)\b|ඔව්|හරි|ஆம்|சரி/i
const NO = /\b(no|nope|don'?t|cancel|not now)\b|නැහැ|එපා|இல்லை|வேண்டாம்/i
const BYE = /\b(bye|goodbye|that'?s all|end( the)? call|thank(s| you))\b|ස්තූතියි|நன்றி/i
const ISSUE = /balance|recharge|reload|rupee|lkr|charge|subscription|vas|alert|data|ශේෂ|රීලෝඩ්|ரீசார்ஜ்|இருப்பு|கட்டணம்/i

/**
 * Advance the conversation with one caller turn. `presented` is the proposal ID the Voice side
 * acknowledged as read aloud; text turns pass the proposal they confirmed explicitly.
 */
export function mockRespond(conversationId: string, text: string, presented: string | null): MockReply {
  const s = load()
  const conv = s.conversations[conversationId]
  if (!conv) throw new Error('unknown conversation')
  const pending = conv.pending_proposal
  const caseId = conv.active_case_id
  let reply: MockReply
  let cards: Card[] = []
  const operationIds: string[] = []

  if (pending && YES.test(text)) {
    if (presented !== pending.id) {
      reply = say('I need to read the offer to you before you can accept it. Shall I read it again?', caseId)
    } else {
      const op: OperationView = {
        ...example<OperationView>('succeeded_operation'),
        id: uuid(),
        case_id: pending.case_id,
        proposal_id: pending.id,
        status: 'PENDING',
        created_at: nowIso(),
        updated_at: nowIso(),
      }
      s.operations[op.id] = { op, createdAt: Date.now() }
      operationIds.push(op.id)
      conv.pending_proposal = null
      reply = {
        ...say(`Okay. I've asked to stop renewals for ${pending.target_label}. I'll tell you when it is confirmed.`, caseId),
        operation_status: 'PENDING',
      }
    }
  } else if (pending && NO.test(text)) {
    conv.pending_proposal = null
    reply = say('No problem, nothing was changed. Is there anything else I can check?', caseId)
  } else if (BYE.test(text)) {
    reply = { ...say('Thanks for calling. Your case stays open if you want to continue by text.', caseId), end_session: true }
  } else if (ISSUE.test(text)) {
    const turn = example<TurnResult>('turn_result')
    cards = turn.cards
    const newCase = caseId ?? uuid()
    conv.active_case_id = newCase
    if (!conv.cases.some((c) => c.id === newCase)) conv.cases.push({ id: newCase, complaint_type: 'BALANCE_RECHARGE', status: 'AWAITING_CUSTOMER' })
    const proposal: ProposalView = { ...example<ProposalView>('proposal'), id: uuid(), case_id: newCase, expires_at: plus(5 * 60_000) }
    conv.pending_proposal = proposal
    const spoken =
      'Your LKR 1,000 recharge posted, and the deductions add up to the LKR 420 you have now. ' +
      `One of them is LKR 60 for ${proposal.target_label}. I can stop it renewing; past charges are not refunded. Shall I go ahead?`
    reply = {
      ...say(spoken, newCase),
      proposal: {
        id: proposal.id,
        proposal_hash: proposal.proposal_hash,
        action_type: proposal.action_type,
        target_label: proposal.target_label,
        consequences: proposal.consequences,
        expires_at: proposal.expires_at,
      },
    }
  } else {
    reply = say('I can help with your balance, recharges, data, connection or service charges. What happened?', caseId)
  }

  const message: MessageView = {
    id: uuid(),
    client_turn_id: uuid(),
    speaker: 'ASSISTANT',
    body: reply.reply_text,
    created_at: nowIso(),
    result: {
      message_id: '',
      conversation_id: conv.id,
      conversation_version: conv.version + 1,
      case_id: reply.case_id,
      reply_text: reply.reply_text,
      cards,
      citations: [],
      pending_question: null,
      operation_ids: operationIds,
      simulation: true,
    },
  } as MessageView
  if (message.result) message.result.message_id = message.id
  conv.messages.push(userMessage(text), message)
  conv.version += 1
  save(s)
  return reply
}

function say(text: string, caseId: string | null): MockReply {
  return { reply_text: text, speech_text: text, case_id: caseId, proposal: null, operation_status: null, end_session: false }
}

function userMessage(text: string): MessageView {
  return { id: uuid(), client_turn_id: uuid(), speaker: 'USER', body: text, created_at: nowIso(), result: null } as MessageView
}

// ---- REST transport ---------------------------------------------------------------------------

export async function mockTransport(_realm: Realm, method: string, path: string, opts: RequestOptions): Promise<RawResponse> {
  await new Promise((r) => setTimeout(r, LATENCY_MS))
  const s = load()
  if (s.outage) return fail(503, 'DEPENDENCY_UNAVAILABLE', 'Mock outage.', true)
  const route = `${method} ${path}`
  let m: RegExpMatchArray | null

  if (route === 'GET /session') {
    if (s.expired) return fail(401, 'SESSION_EXPIRED', 'Mock session expired.')
    return s.session ? ok(200, s.session) : fail(401, 'UNAUTHENTICATED', 'No session.')
  }
  if (route === 'POST /demo/sessions') {
    const body = opts.body as { demo_identity?: string; credential?: string }
    if (!body?.demo_identity?.trim() || !body?.credential) return fail(422, 'VALIDATION_ERROR', 'Enter both fields.')
    s.session = { ...example<SessionView>('customer_session'), id: uuid(), expires_at: plus(30 * 60_000), csrf_token: uuid() }
    s.expired = false
    save(s)
    return ok(200, s.session)
  }
  if (route === 'DELETE /session') {
    s.session = null
    save(s)
    return ok(204, null)
  }
  if (!s.session) return fail(401, 'UNAUTHENTICATED', 'No session.')

  if (route === 'POST /conversations') {
    const conv: ConversationView = {
      ...example<ConversationView>('conversation'),
      id: uuid(),
      version: 1,
      language: (opts.body as { language: ConversationView['language'] }).language,
      active_case_id: null,
      expires_at: plus(30 * 60_000),
      messages: [],
      cases: [],
      pending_question: null,
      pending_proposal: null,
    }
    s.conversations[conv.id] = conv
    save(s)
    return ok(201, conv)
  }
  if ((m = route.match(/^GET \/conversations\/([^/]+)$/))) {
    const conv = s.conversations[m[1]]
    return conv ? ok(200, conv) : fail(404, 'RESOURCE_NOT_FOUND', 'No such conversation.')
  }
  if ((m = route.match(/^POST \/conversations\/([^/]+)\/voice-sessions$/))) {
    if (!s.conversations[m[1]]) return fail(404, 'RESOURCE_NOT_FOUND', 'No such conversation.')
    const voiceSessionId = uuid()
    const grant: VoiceSessionGrant = {
      ...example<VoiceSessionGrant>('voice_grant'),
      binding_id: uuid(),
      voice_session_id: voiceSessionId,
      browser_grant: uuid(),
      websocket_path: `/ws/hutch/${voiceSessionId}`,
      websocket_url: `mock://voice/${m[1]}`,
      expires_at: Math.floor(Date.now() / 1000) + 60,
    }
    return ok(201, grant)
  }
  if ((m = route.match(/^POST \/conversations\/([^/]+)\/messages$/))) {
    const conv = s.conversations[m[1]]
    if (!conv) return fail(404, 'RESOURCE_NOT_FOUND', 'No such conversation.')
    const req = opts.body as MessageRequest
    if (req.expected_version !== conv.version) return fail(409, 'STALE_VERSION', 'Conversation changed.')
    const input = req.input
    const text =
      input.type === 'text'
        ? input.text
        : input.type === 'action_decision'
          ? input.decision === 'ACCEPT'
            ? 'yes'
            : 'no'
          : 'help'
    const presented = input.type === 'action_decision' ? input.proposal_id : null
    mockRespond(conv.id, text, presented)
    const updated = load().conversations[conv.id]
    return ok(200, updated.messages.at(-1)?.result)
  }
  if ((m = route.match(/^GET \/operations\/([^/]+)$/))) {
    const entry = s.operations[m[1]]
    if (!entry) return fail(404, 'RESOURCE_NOT_FOUND', 'No such operation.')
    const done = Date.now() - entry.createdAt > OPERATION_MS
    return ok(200, done ? { ...entry.op, status: 'SUCCEEDED', updated_at: nowIso() } : entry.op)
  }
  if ((m = route.match(/^GET \/cases\/([^/]+)\/receipt$/))) return ok(200, { ...example<object>('receipt'), case_id: m[1] })
  if ((m = route.match(/^GET \/cases\/([^/]+)$/))) return ok(200, { ...example<object>('case'), id: m[1] })

  return fail(404, 'RESOURCE_NOT_FOUND', `Mock has no route for ${route}.`)
}

/** Buttons in the simulation banner use these to reach hard-to-trigger states. */
export const mockControls = {
  get outage() {
    return load().outage
  },
  setOutage(on: boolean) {
    save({ ...load(), outage: on })
  },
  expireSession() {
    save({ ...load(), expired: true, session: null })
  },
  reset() {
    try {
      sessionStorage.removeItem(KEY)
    } catch {
      /* nothing to clear */
    }
  },
}
