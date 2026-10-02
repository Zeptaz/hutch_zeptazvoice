// Friendly aliases over the generated contract types (src/api/schema.d.ts).
// Regenerate the schema with `npm run gen:api` whenever contracts/openapi.json changes.
import type { components } from './schema'

type Schemas = components['schemas']

export type Language = Schemas['Language']
export type ComplaintType = Schemas['ComplaintType']
export type EvidenceState = Schemas['EvidenceState']
export type OperationStatus = Schemas['OperationStatus']
export type ReviewStatus = Schemas['ReviewStatus']
export type DeliveryState = Schemas['DeliveryState']

export type ApiErrorBody = Schemas['Error']
export type LoginRequest = Schemas['LoginRequest']
export type SessionView = Schemas['SessionView']

export type ConversationView = Schemas['ConversationView']
export type MessageView = Schemas['MessageView']
export type MessageRequest = Schemas['MessageRequest']
export type TurnResult = Schemas['TurnResult']
export type TurnInput = Schemas['TurnInput']
export type PendingQuestion = Schemas['PendingQuestion']
export type Citation = Schemas['Citation']
export type Card = Schemas['Card']
export type CardOf<T extends Card['type']> = Extract<Card, { type: T }>

export type InvestigationResult = Schemas['InvestigationResult']
export type Calculation = Schemas['Calculation']
export type Finding = Schemas['Finding']
export type ProposalView = Schemas['ProposalView']
export type Decision = Schemas['Decision']
export type OperationView = Schemas['OperationView']
export type Handoff = Schemas['Handoff']
export type ReceiptView = Schemas['ReceiptView']
export type ReportedFacts = Schemas['ReportedFacts']

export type AccountView = Schemas['AccountView']
export type CaseView = Schemas['CaseView']

export type VoiceSessionGrant = Schemas['VoiceSessionGrant']
/** Six-field proposal projection Voice receives (narrower than ProposalView). */
export type VoiceProposal = Schemas['Proposal']

// Voice WebSocket protocol `zeptaz-hutch-v2` (VoiceServerControl / VoiceClientControl in the contract).
// Assistant transcripts also carry response_id at runtime (Voice app.py), so it is optional here.
export type VoiceServerMessage =
  | Schemas['VoiceReady']
  | Schemas['VoiceGreeting']
  | (Schemas['VoiceTranscript'] & { response_id?: string })
  | Schemas['VoiceResolveResult']
  | Schemas['VoiceProposalAck']
  | Schemas['VoiceError']
  | Schemas['VoiceEnded']
  | Schemas['VoiceInterrupted']
  | Schemas['VoiceAudioStart']
  | Schemas['VoiceAudioEnd']
  | Schemas['VoiceAudioFallback']
  | Schemas['VoicePlaybackAck']
export type VoiceResolveResult = Schemas['VoiceResolveResult']
export type VoiceClientMessage = Schemas['VoicePlaybackComplete'] | Schemas['VoicePresentation']
