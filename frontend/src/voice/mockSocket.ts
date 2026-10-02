// In-browser stand-in for the Voice WebSocket (mock mode). Speaks protocol zeptaz-hutch-v2 the way
// Voice app.py does: user transcripts, resolve_result, audio framed by audio_start/audio_end, sensitive
// replies held until complete, playback/proposal acknowledgements, interruption and end of call.
// Caller speech is simulated with mockVoice.say(); replies play a soft tone instead of speech.
import { mockRespond, type MockReply } from '@/api/mock'
import type { VoiceServerMessage } from '@/api/types'
import type { VoiceSocket } from './socket'

const RATE = 24000
const CHUNK_SAMPLES = 2400 // 100 ms
const SESSION_LIMIT_MS = 120_000

type Reply = {
  id: string
  proposal: MockReply['proposal']
  sensitive: boolean
  sentAudio: boolean
  complete: boolean
  playbackAcked: boolean
  interrupted: boolean
  endSession: boolean
}

class MockVoiceSocket implements VoiceSocket {
  binaryType: BinaryType = 'arraybuffer'
  readyState: number = WebSocket.CONNECTING
  protocol = 'zeptaz-hutch-v2'
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string | ArrayBuffer }) => void) | null = null
  onclose: ((e: { code: number; reason: string }) => void) | null = null
  onerror: (() => void) | null = null

  private conversationId: string
  private reply: Reply | null = null
  private presented: string | null = null
  private timers = new Set<number>()

  constructor(url: string) {
    this.conversationId = url.split('/').pop() ?? ''
    this.later(250, () => {
      this.readyState = WebSocket.OPEN
      this.onopen?.()
      this.emit({
        type: 'ready',
        session_id: crypto.randomUUID(),
        provider: 'gemini_live',
        model: 'mock',
        live_profile: 'mock',
        features: { full_duplex: true, context_compression: false, protocol_version: 2 },
        input_format: { encoding: 'pcm_s16le', sample_rate: 16000 },
        output_format: { encoding: 'pcm_s16le', sample_rate: 24000 },
      })
      this.emit({ type: 'greeting', text: 'HUTCH Resolve demo. Tell me what you need help with.' })
    })
    this.later(SESSION_LIMIT_MS, () => this.end('session_limit'))
    mockVoice.active = this
  }

  send(data: string | ArrayBuffer) {
    if (typeof data !== 'string') return // caller audio: nothing to transcribe in mock mode
    const msg = JSON.parse(data) as { type: string; response_id: string; proposal_id?: string; proposal_hash?: string }
    const r = this.reply
    const matching = !!r && r.id === msg.response_id && !r.interrupted
    if (msg.type === 'playback_complete') {
      const accepted = matching && r!.complete && r!.sentAudio && !r!.playbackAcked
      if (accepted) r!.playbackAcked = true
      this.emit({ type: 'playback_ack', response_id: msg.response_id, accepted })
      if (accepted && r!.endSession) this.later(300, () => this.end('resolve_requested'))
    } else if (msg.type === 'proposal_presented') {
      const accepted = matching && !!r!.proposal && r!.playbackAcked && r!.proposal.id === msg.proposal_id && r!.sensitive
      if (accepted) this.presented = msg.proposal_id!
      this.emit({ type: 'proposal_ack', response_id: msg.response_id, accepted })
    }
  }

  close(code = 1000, reason = '') {
    if (this.readyState === WebSocket.CLOSED) return
    this.readyState = WebSocket.CLOSED
    this.timers.forEach((t) => window.clearTimeout(t))
    this.timers.clear()
    if (mockVoice.active === this) mockVoice.active = null
    this.onclose?.({ code, reason })
  }

  /** A finalized caller turn, as if Gemini transcribed it. */
  say(text: string) {
    if (this.readyState !== WebSocket.OPEN) return
    this.cancelReply(false) // a new turn keeps the acknowledged offer for this turn (app.py revoke_reply)
    this.emit({ type: 'transcript', speaker: 'user', text, final: true })
    this.later(900, () => {
      const presented = this.presented
      this.presented = null // attached once to the next final turn
      const result = mockRespond(this.conversationId, text, presented)
      const id = crypto.randomUUID()
      const sensitive = !!(result.proposal || result.operation_status)
      this.reply = { id, proposal: result.proposal, sensitive, sentAudio: false, complete: false, playbackAcked: false, interrupted: false, endSession: result.end_session }
      this.emit({ type: 'resolve_result', response_id: id, ...result, pending_question: null, sensitive_audio: sensitive })
      if (sensitive && mockVoice.failSpeechCheck) {
        this.emit({ type: 'audio_fallback', response_id: id, text: result.speech_text, reason: 'speech_verification_failed' })
        this.reply.complete = true
        if (result.end_session) this.later(800, () => this.end('resolve_requested'))
        return
      }
      this.speak(this.reply, result.speech_text)
    })
  }

  /** Barge-in: Voice tells the browser to drop queued audio and any pending acknowledgement. */
  interrupt() {
    const r = this.reply
    if (!r || r.playbackAcked) return
    this.cancelReply(true)
    this.emit({ type: 'interrupted', response_id: r.id })
  }

  private cancelReply(clearPresentation: boolean) {
    if (this.reply) this.reply.interrupted = true
    if (clearPresentation) this.presented = null
  }

  private speak(reply: Reply, text: string) {
    const pcm = tone(text)
    const chunks = Math.ceil(pcm.length / CHUNK_SAMPLES)
    this.emit({ type: 'audio_start', response_id: reply.id })
    reply.sentAudio = true
    // Sensitive replies arrive in one burst after verification; others stream at about real time.
    const gap = reply.sensitive ? 0 : 90
    for (let i = 0; i < chunks; i++) {
      this.later(i * gap, () => {
        if (reply.interrupted) return
        this.onmessage?.({ data: pcm.slice(i * CHUNK_SAMPLES, (i + 1) * CHUNK_SAMPLES).buffer })
      })
    }
    this.later(chunks * gap + 10, () => {
      if (reply.interrupted) return
      this.emit({ type: 'audio_end', response_id: reply.id })
      reply.complete = true
    })
  }

  private end(reason: string) {
    if (this.readyState !== WebSocket.OPEN) return
    this.emit({ type: 'ended', reason })
    this.close(1000, reason)
  }

  private emit(msg: VoiceServerMessage) {
    if (this.readyState !== WebSocket.OPEN) return
    this.onmessage?.({ data: JSON.stringify(msg) })
  }

  private later(ms: number, fn: () => void) {
    const t = window.setTimeout(() => {
      this.timers.delete(t)
      fn()
    }, ms)
    this.timers.add(t)
  }
}

/** A quiet, speech-paced tone: about a third of a second per word, capped at 6 seconds. */
function tone(text: string): Int16Array {
  const words = text.split(/\s+/).filter(Boolean).length
  const seconds = Math.min(6, Math.max(0.8, words * 0.32))
  const out = new Int16Array(Math.round(seconds * RATE))
  for (let i = 0; i < out.length; i++) {
    const t = i / RATE
    const syllable = Math.max(0, Math.sin(2 * Math.PI * 3.2 * t)) ** 0.6
    const v = (Math.sin(2 * Math.PI * 196 * t) + 0.4 * Math.sin(2 * Math.PI * 294 * t)) * 0.05 * syllable
    out[i] = v * 0x7fff
  }
  return out
}

/** Handle for the mock caller panel. */
export const mockVoice = {
  active: null as MockVoiceSocket | null,
  failSpeechCheck: false,
  say(text: string) {
    this.active?.say(text)
  },
  interrupt() {
    this.active?.interrupt()
  },
}

export function openMockSocket(url: string): VoiceSocket {
  return new MockVoiceSocket(url)
}
