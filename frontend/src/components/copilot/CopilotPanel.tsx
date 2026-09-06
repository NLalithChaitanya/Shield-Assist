/**
 * CopilotPanel — conversational chat interface for the case assistant.
 *
 * Session memory: conversation history persists in component state while
 * the merchant has the case open, and resets on page reload or navigation.
 * No database table, no cross-session persistence.
 *
 * On mount: greets the merchant with a 1-2 sentence summary from real data.
 * On input: routes the question to the best-matching ability (Explain,
 * Investigate, Recommend, or Draft) and streams the response.
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import { Send, Loader2, ChevronDown, ChevronRight, MessageCircle, Search, Lightbulb, FileText } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { streamCopilot, type CopilotAbility, type CopilotError } from '../../lib/api';
import type { DisputeDetail } from '../../lib/types';

interface CopilotPanelProps {
  dispute: DisputeDetail;
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  ability?: CopilotAbility;
}

// ─── Site navigation structure (mirrors Sidebar.tsx exactly) ─────────
// Kept here so the assistant can reference real nav items without
// importing the Sidebar component (which has React dependencies).
// If the sidebar changes, update this map to match.

interface NavItem {
  label: string;
  path: string;
  description: string;
}

const SITE_NAV: NavItem[] = [
  { label: 'Overview', path: '/', description: 'Dashboard with dispute summary and metrics' },
  { label: 'All disputes', path: '/disputes', description: 'Full list of every dispute, sorted by priority' },
  { label: 'Act now', path: '/disputes?filter=act_now', description: 'Disputes that need immediate action (past deadline or urgent)' },
  { label: 'Review', path: '/disputes?filter=review', description: 'Disputes ready for your review before submission' },
  { label: 'Low priority', path: '/disputes?filter=low_priority', description: 'Disputes with lower urgency, can be addressed later' },
  { label: 'Documents', path: '/evidence', description: 'Upload and manage evidence documents' },
  { label: 'Activity', path: '/audit', description: 'Audit trail of all actions taken on your cases' },
  { label: 'Settings', path: '/settings', description: 'Account and app settings' },
];

// ─── Intent matching ────────────────────────────────────────────────
// Simple keyword routing — not sophisticated, but covers the main cases.
//
// ORDER MATTERS: ability-specific intents are checked FIRST so they
// aren't swallowed by broad navigation keywords. Navigate is LAST
// (a fallback for pure "where is the sidebar" / "go to page" questions).
//
// Why this order:
//   0. chitchat — exact-match greetings/small talk (no backend call)
//   1. investigate — most specific (contradiction/conflict words)
//   2. recommend — "what should I upload" / "strengthen my case"
//   3. draft — "write a response" / "submit"
//   4. explain — broad ability catch-all ("why", "strength", "score")
//   5. navigate — only pure UI-navigation phrasing, checked last

interface IntentRule {
  ability: CopilotAbility;
  keywords: string[];
}

const INTENT_RULES: IntentRule[] = [
  {
    ability: 'investigate',
    keywords: [
      'contradiction', 'mismatch', 'conflict', 'inconsistent', 'wrong',
      'doesn\'t match', 'doesnt match', 'disagree', 'problem',
      'flag', 'wrong document', 'wrong file',
    ],
  },
  {
    ability: 'recommend',
    keywords: [
      'recommend', 'suggest', 'what should i', 'what can i',
      'improve', 'strengthen', 'evidence should', 'document should',
      'settle', 'contest this', 'expire', 'strategy',
    ],
  },
  {
    ability: 'draft',
    keywords: [
      'draft', 'write a', 'response for', 'reply to', 'submit',
      'formal', 'letter', 'template',
    ],
  },
  {
    ability: 'explain',
    keywords: [
      'explain', 'why', 'strength', 'weak', 'strong', 'score',
      'probability', 'win', 'mean', 'tell me about', 'summary',
      'overview of', 'status of', 'what\'s going on', 'whats going on',
      'happening', 'what happened', 'what\'s happened',
      'how did', 'what do', 'how is', 'how was',
      'what\'s the', 'whats the',
      'show me', 'tell me', 'describe',
    ],
  },
  {
    ability: 'navigate',
    keywords: [
      'sidebar', 'menu', 'go to', 'navigate to',
      'open the', 'find the page', 'which section', 'which page',
      'how do i get to', 'how do i open',
      'view all disputes page', 'go back to',
      'see all my', 'find urgent', 'upload history',
      'actions been', 'taken on my',
    ],
  },
];

// ─── Chitchat patterns ─────────────────────────────────────────────
// Exact-match greetings and small talk — no backend call, handled locally.
// Checked before keyword rules so "hi" doesn't fall through to explain.

const CHITCHAT_WORDS = new Set([
  'hi', 'hey', 'hello', 'howdy', 'hiya', 'yo',
  'thanks', 'thank you', 'thx', 'ty',
  'ok', 'okay', 'k', 'sure', 'got it',
  'bye', 'goodbye', 'see ya', 'talk later',
  'help', 'what can you do', 'who are you', 'what do you do',
]);

const CHITCHAT_REPLIES = [
  "Hey! What can I help you with on this case?",
  "Hi there! I'm here to help with this dispute. What would you like to know?",
  "Hello! Ask me anything about this case — I can explain scores, check for issues, or suggest next steps.",
  "Hey! What's on your mind about this case?",
];

function isChitchat(text: string): boolean {
  const trimmed = text.trim().toLowerCase().replace(/[!?.]+$/g, '');
  return CHITCHAT_WORDS.has(trimmed);
}

function buildChitchatResponse(): string {
  return CHITCHAT_REPLIES[Math.floor(Math.random() * CHITCHAT_REPLIES.length)];
}

function matchIntent(question: string): CopilotAbility | 'off_topic' {
  const lower = question.toLowerCase();

  // 0. Off-topic detection FIRST: if the question contains zero dispute/evidence/site
  //    related words, it's probably not something Ally can help with.
  //    Must run before ability keywords to prevent broad matches like
  //    "what's the weather" matching "what's the" in explain.
  const DISPUTE_SIGNALS = [
    'case', 'dispute', 'charge', 'payment', 'refund', 'order', 'customer',
    'evidence', 'document', 'upload', 'score', 'strength', 'probability',
    'weak', 'strong', 'missing', 'issue', 'problem', 'contradiction',
    'mismatch', 'date', 'amount', 'rupee', 'inr', 'razorpay', 'response',
    'submit', 'contest', 'settle', 'deadline', 'compensation', 'goods',
    'service', 'delivery', 'return', 'cancel', 'billing', 'invoice',
    'receipt', 'terms', 'conditions', 'policy', 'communication', 'chat',
    'email', 'message', 'call', 'this', 'that', 'it', // short pronouns often refer to the case
    // Quality words that describe the case itself -- without these, a
    // question like "what's wrong" fails the signal gate and is wrongly
    // answered by the canned off-topic reply instead of the LLM.
    'wrong', 'incorrect', 'inconsistent', 'invalid', 'consistency',
    'completeness', 'quality', 'matter',
    'explain', 'investigate', 'recommend', 'draft', 'suggest',
  ];
  const hasDisputeSignal = DISPUTE_SIGNALS.some(w => lower.includes(w));

  if (!hasDisputeSignal) {
    return 'off_topic';
  }

  // 1. Check ability-specific keywords
  for (const rule of INTENT_RULES) {
    for (const kw of rule.keywords) {
      if (lower.includes(kw)) return rule.ability;
    }
  }

  // 2. Default: explain is the safest fallback for case-related questions
  return 'explain';
}

// ─── Off-topic redirect ─────────────────────────────────────────────

const OFF_TOPIC_RESPONSE =
  "I'm here to help with your payment disputes and evidence -- I'm not much use for general questions. " +
  "Ask me about this case, what documents to upload, or where to find things in Shield Assist!";

// ─── Navigation response builder ────────────────────────────────────
// Matches the user's question to relevant nav items and generates
// guidance text using the real site structure.

function buildNavResponse(question: string): string {
  const lower = question.toLowerCase();

  // Find nav items that match the question
  const matches = SITE_NAV.filter(item => {
    const labelLower = item.label.toLowerCase();
    // Direct label match
    if (lower.includes(labelLower)) return true;

    // Keyword-based matching (individual words + phrases)
    const keywords: Record<string, string[]> = {
      'Overview': ['dashboard', 'summary', 'metrics', 'overview'],
      'All disputes': ['all disputes', 'every dispute', 'full list', 'all cases', 'past dispute', 'previous dispute', 'see all'],
      'Act now': ['urgent', 'act now', 'immediate', 'overdue', 'past deadline', 'find urgent'],
      'Review': ['review', 'ready to review', 'before submit'],
      'Low priority': ['low priority', 'later', 'less urgent'],
      'Documents': ['upload', 'document', 'evidence', 'file', 'attachment', 'proof'],
      'Activity': ['activity', 'audit', 'history', 'log', 'timeline', 'past actions', 'actions taken', 'actions been', 'taken on my', 'what actions', 'what happened', 'taken on'],
      'Settings': ['settings', 'account', 'preferences', 'config'],
    };

    const itemKeywords = keywords[item.label] || [];
    return itemKeywords.some(kw => lower.includes(kw));
  });

  if (matches.length === 0) {
    // No specific match — give a general overview of where to find things
    const navList = SITE_NAV.map(item => `- **${item.label}**: ${item.description}`).join('\n');
    return `I can help you navigate the app. Here's what's available in the sidebar on the left:\n\n${navList}\n\nWhich section are you looking for?`;
  }

  // Build targeted response
  const lines = matches.map(item => {
    return `- Click **${item.label}** in the sidebar — ${item.description}.`;
  });

  // Add a helpful follow-up
  let followUp = '';
  if (matches.some(m => m.label === 'Documents')) {
    followUp = '\n\nOn the Documents page, you can drag and drop files or click "choose file" to upload. Accepted formats are PDF, PNG, and JPG.';
  } else if (matches.some(m => m.label === 'All disputes')) {
    followUp = '\n\nYou can also use the filtered views (Act now, Review, Low priority) to focus on specific categories.';
  } else if (matches.some(m => m.label === 'Activity')) {
    followUp = '\n\nThis shows every action taken on your cases — uploads, scoring, copilot interactions, and more.';
  }

  return `Here's where to find that:\n\n${lines.join('\n')}${followUp}`;
}

// ─── Greeting builder ───────────────────────────────────────────────

function buildGreeting(dispute: DisputeDetail): string {
  const scores = dispute.scores;
  const missing = scores?.missing_required_slots?.length ?? 0;
  const contradictions = scores?.contradiction_flags?.length ?? 0;

  let summary = '';

  if (missing > 0 && contradictions > 0) {
    summary = `This case needs some work before we can prepare a response — you're missing ${missing} document${missing > 1 ? 's' : ''} Razorpay requires, and there ${contradictions === 1 ? "is an issue" : "are some issues"} in your uploaded documents that need checking.`;
  } else if (missing > 0) {
    summary = `This case needs ${missing} more document${missing > 1 ? 's' : ''} before we can prepare a response. Upload what's missing and we'll get started.`;
  } else if (contradictions > 0) {
    summary = `Your documents are all uploaded, but there ${contradictions === 1 ? "is a mismatch" : "are some mismatches"} we need to sort out before submitting.`;
  } else {
    summary = `Your documents look complete and consistent. We're in good shape to move forward.`;
  }

  return `Hey there! I'm Ally, your dispute copilot. ${summary} What would you like to know about this case?`;
}

// ─── Component ──────────────────────────────────────────────────────

export default function CopilotPanel({ dispute }: CopilotPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [expanded, setExpanded] = useState(true);
  const [greeted, setGreeted] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const streamingCleanupRef = useRef<(() => void) | null>(null);
  // Track which abilities have been shown this session to detect repeats
  // (buttons) and which exact question wordings were already asked
  // (typed messages), so a different question on the same topic is not
  // answered with a canned "I just covered that" redirect.
  const shownAbilitiesRef = useRef<Set<CopilotAbility>>(new Set());
  const askedQuestionsRef = useRef<Set<string>>(new Set());

  const normalizeQuestion = (text: string): string =>
    text.trim().toLowerCase().replace(/[!?.]+$/g, '').replace(/\s+/g, ' ');

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Greet on mount
  useEffect(() => {
    if (greeted) return;
    setGreeted(true);
    const greeting: ChatMessage = {
      id: 'greeting',
      role: 'assistant',
      text: buildGreeting(dispute),
    };
    setMessages([greeting]);
  }, [dispute, greeted]);

  // ─── Ability-specific quota fallback text ────────────────────────
  // When daily Gemini quota is exhausted, show the merchant something
  // useful instead of a blank error.  These reference data visible
  // on the page itself.

  const QUOTA_FALLBACK: Record<string, string> = {
    explain:
      "I'm temporarily unable to pull fresh details right now, but you can check your " +
      "Case Strength and score breakdown directly on this page for the full picture. " +
      "The Missing Documents and Issues To Check sections above also show exactly what needs attention.",
    investigate:
      "I'm temporarily unable to analyze your documents right now, but the " +
      "Issues To Check section on this page already lists the contradictions and mismatches " +
      "found in your evidence. Take a look at that list for the details.",
    recommend:
      "I'm temporarily unable to generate recommendations right now, but the " +
      "Missing Documents section on this page shows exactly what you still need to upload. " +
      "Focus on filling those gaps first -- that's the highest-impact move.",
    draft:
      "I'm temporarily unable to draft a response right now. You'll need to prepare this " +
      "manually -- check the score breakdown and issues list on this page for context.",
  };

  // ─── Build the conversation history sent to the backend ─────────────
  // Only real turns are included: the merchant's typed questions plus the
  // model's streamed answers (tagged with a 4-ability). Local, non-model
  // messages (greeting, chitchat, off-topic, navigation, dedup redirects)
  // are excluded so the LLM isn't fed canned text as "its own" replies.
  const buildHistory = useCallback((): { role: 'user' | 'assistant'; text: string }[] => {
    return messages
      .filter(m => m.id !== 'greeting')
      .filter(m => {
        if (m.role === 'user') return true;
        if (m.ability === undefined || m.ability === 'navigate') return false;
        return !m.text.startsWith('I just covered that above');
      })
      .slice(-6)
      .map(m => ({ role: m.role, text: m.text }));
  }, [messages]);

  // ─── Core ability trigger (shared by typed messages AND button clicks) ───
  const triggerAbility = useCallback((ability: CopilotAbility, userText?: string) => {
    // Add user message if this was a typed message (not a button click)
    if (userText) {
      const userMsg: ChatMessage = {
        id: `user-${Date.now()}`,
        role: 'user',
        text: userText,
      };
      setMessages(prev => [...prev, userMsg]);
    }

    // Navigation is handled locally — no backend call needed
    if (ability === 'navigate') {
      const navResponse: ChatMessage = {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        text: buildNavResponse(userText || ''),
        ability,
      };
      setMessages(prev => [...prev, navResponse]);
      return;
    }

    // Duplicate detection:
    //  - Buttons (no typed text): running the same ability twice would
    //    replay the word-for-word identical cached answer — skip it.
    //  - Typed questions: only an EXACT repeat of the same wording is
    //    suppressed; a different question on the same topic is allowed
    //    through so the merchant gets a real (re)generated answer.
    const normalizedQuestion = userText ? normalizeQuestion(userText) : '';
    const isButtonRepeat = !userText && shownAbilitiesRef.current.has(ability);
    const isExactQuestionRepeat = !!userText && askedQuestionsRef.current.has(normalizedQuestion);

    if (isButtonRepeat || isExactQuestionRepeat) {
      const redirectMsg: ChatMessage = {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        text: `I just covered that above — take a look at my earlier response. Let me know if anything's unclear, or ask me something else about this case.`,
        ability,
      };
      setMessages(prev => [...prev, redirectMsg]);
      return;
    }

    // Mark this ability / question as seen
    shownAbilitiesRef.current.add(ability);
    if (normalizedQuestion) askedQuestionsRef.current.add(normalizedQuestion);

    // Stream from backend
    const assistantMsg: ChatMessage = {
      id: `assistant-${Date.now()}`,
      role: 'assistant',
      text: '',
      ability,
    };
    setMessages(prev => [...prev, assistantMsg]);
    setStreaming(true);

    const cleanup = streamCopilot(
      dispute.dispute_id,
      ability,
      (token) => {
        setMessages(prev => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last.role === 'assistant') {
            updated[updated.length - 1] = { ...last, text: last.text + token };
          }
          return updated;
        });
      },
      () => {
        setStreaming(false);
        streamingCleanupRef.current = null;
      },
      (err: CopilotError) => {
        // Distinguish failure modes -- never show raw error text to merchants:
        //   1. Rate limit / service unavailable / circuit breaker (recoverable) → friendly retry prompt
        //   2. Quota exhausted (not recoverable today) → ability-specific static fallback
        //   3. Unknown → generic "something went wrong" (no raw error text exposed)
        let friendly: string;
        if (err.errorType === 'quota_exceeded') {
          friendly = QUOTA_FALLBACK[ability] || QUOTA_FALLBACK.explain;
        } else if (err.errorType === 'rate_limit') {
          friendly = "I'm getting a lot of requests right now -- give me a quick second to catch up and try again.";
        } else if (err.errorType === 'service_unavailable') {
          friendly = "Ally is temporarily unavailable because the AI service is experiencing high demand. Please retry in a few seconds.";
        } else if (err.errorType === 'circuit_breaker') {
          friendly = "I'm recovering from a brief hiccup -- let me try again in a moment.";
        } else if (err.errorType === 'connection') {
          friendly = "Looks like the connection dropped. Let me try that again.";
        } else {
          friendly = "Something went wrong. Please try again in a moment.";
        }
        setMessages(prev => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last.role === 'assistant') {
            const text = last.text.trim()
              ? last.text + `\n\n${friendly}`
              : friendly;
            updated[updated.length - 1] = { ...last, text };
          }
          return updated;
        });
        setStreaming(false);
        streamingCleanupRef.current = null;
      },
      {
        // Send the merchant's actual question + prior turns so the answer
        // is tailored to what was asked (not a fixed ability script).
        question: userText,
        history: buildHistory(),
      },
    );

    streamingCleanupRef.current = cleanup;
  }, [dispute.dispute_id, buildHistory]);

  const sendMessage = useCallback(() => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput('');

    // Check chitchat first (exact match, no backend call)
    if (isChitchat(text)) {
      const userMsg: ChatMessage = { id: `user-${Date.now()}`, role: 'user', text };
      const chitchatMsg: ChatMessage = {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        text: buildChitchatResponse(),
      };
      setMessages(prev => [...prev, userMsg, chitchatMsg]);
      return;
    }

    const ability = matchIntent(text);

    // Off-topic: not about disputes/evidence/site
    if (ability === 'off_topic') {
      const userMsg: ChatMessage = { id: `user-${Date.now()}`, role: 'user', text };
      const offTopicMsg: ChatMessage = {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        text: OFF_TOPIC_RESPONSE,
      };
      setMessages(prev => [...prev, userMsg, offTopicMsg]);
      return;
    }

    // Route through the shared trigger (handles dedup, navigation, streaming)
    triggerAbility(ability, text);
  }, [input, streaming, triggerAbility]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  // ─── Quick-action button handler ───────────────────────────────
  const handleQuickAction = useCallback((ability: CopilotAbility) => {
    if (streaming) return;
    // Button clicks don't add a user message — the button IS the intent
    triggerAbility(ability);
  }, [streaming, triggerAbility]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      streamingCleanupRef.current?.();
    };
  }, []);

  return (
    <div className="border border-line rounded-lg bg-surface-raised">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-3 border-b border-line hover:bg-surface-overlay transition-colors"
      >
        <div className="flex items-center gap-2">
          <MessageCircle size={13} className="text-ink-faint" />
          <div className="text-[10px] font-semibold text-ink-faint uppercase tracking-wider">
            Ally — Dispute Copilot
          </div>
        </div>
        {expanded ? <ChevronDown size={12} className="text-ink-faint" /> : <ChevronRight size={12} className="text-ink-faint" />}
      </button>

      {expanded && (
        <div className="flex flex-col" style={{ height: '400px' }}>
          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {messages.map(msg => (
              <div
                key={msg.id}
                className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`max-w-[85%] rounded-lg px-3 py-2 text-[12px] leading-relaxed ${
                    msg.role === 'user'
                      ? 'bg-signal-bg text-ink border border-signal/20 whitespace-pre-wrap'
                      : 'bg-surface-sunken text-ink'
                  }`}
                >
                  {/* Assistant replies are Markdown (Gemini/Groq) -- render it
                      as formatted text; user messages stay plain. */}
                  {msg.role === 'assistant' ? (
                    <div className="chat-md">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
                    </div>
                  ) : (
                    msg.text
                  )}
                  {streaming && msg.role === 'assistant' && msg.id === messages[messages.length - 1]?.id && (
                    <span className="stream-cursor" />
                  )}
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          {/* Quick-action buttons */}
          <div className="border-t border-line px-3 pt-2 pb-1 flex gap-1.5">
            <button
              onClick={() => handleQuickAction('explain')}
              disabled={streaming}
              className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium bg-surface-sunken border border-line text-ink-faint hover:text-ink hover:border-signal/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Lightbulb size={10} />
              Explain
            </button>
            <button
              onClick={() => handleQuickAction('investigate')}
              disabled={streaming}
              className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium bg-surface-sunken border border-line text-ink-faint hover:text-ink hover:border-signal/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Search size={10} />
              Investigate
            </button>
            <button
              onClick={() => handleQuickAction('recommend')}
              disabled={streaming}
              className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium bg-surface-sunken border border-line text-ink-faint hover:text-ink hover:border-signal/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <FileText size={10} />
              Recommend
            </button>
          </div>

          {/* Input */}
          <div className="border-t border-line p-3">
            <div className="flex gap-2">
              <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask Ally about this case..."
                disabled={streaming}
                className="flex-1 bg-surface-sunken border border-line rounded-md px-3 py-2 text-[12px] text-ink placeholder:text-ink-faint focus:outline-none focus:border-signal/40 disabled:opacity-50"
              />
              <button
                onClick={sendMessage}
                disabled={!input.trim() || streaming}
                className="px-3 py-2 bg-signal-bg text-signal border border-signal/30 rounded-md text-[12px] font-medium hover:bg-signal/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {streaming ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Send size={13} />
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
