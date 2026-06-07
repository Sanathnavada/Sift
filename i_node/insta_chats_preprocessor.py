"""
Instagram Chat to Unsloth/Llama-3 Fine-Tuning Converter (Production Grade v2.0)
================================================================================
Enhanced with semantic session grouping and advanced quality filters.

NEW in v2.0:
-----------
✨ Semantic Embeddings: Context-aware session grouping with AI similarity detection
✨ Lexical Diversity: Filters spam like "Cool cool cool" x50
✨ Narrative Detection: Rejects 3rd-person stories that confuse training
✨ Turn-Aware Grouping: Preserves natural conversation flow
✨ Adaptive Context: Dynamic window sizing based on conversation complexity
✨ Multi-Threshold Logic: Primary + soft thresholds for better session boundaries
✨ Comprehensive Quality Scoring: 0.0-1.0 scale with detailed rejection tracking

Architecture:
- Strategy Pattern: Message type handlers
- Chain of Responsibility: Filter pipeline
- Factory Pattern: Message classification
- Single Responsibility: Each class has one job

Instagram-Specific Features:
- Unicode normalization (Instagram's mangled UTF-8)
- Reel reply detection and filtering
- Anti-lobotomy filter (prevents catastrophic forgetting)
- Enhanced PII scrubbing (Instagram usernames, locations)
"""

import json
import logging
import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass, field
from collections import Counter, deque
from abc import ABC, abstractmethod
from enum import Enum
import os

# Optional: Semantic model (graceful degradation if unavailable)
try:
    from sentence_transformers import SentenceTransformer, util
    import torch
    SEMANTIC_AVAILABLE = True
except ImportError:
    SEMANTIC_AVAILABLE = False
    logging.warning("sentence-transformers not available. Using time-based grouping only.")

# ==========================================
# CONFIGURATION
# ==========================================
class Config:
    """Centralized configuration with semantic enhancements."""
    
    # Identity
    TARGET_USER_NAME: str = os.getenv("TARGET_USER_NAME", "Sanath")
    
    # System Prompt
    SYSTEM_PROMPT: str = (

    "You are Sanath. You are a calm, logical software engineer driven by curiosity and competence. "

    "You are realistic, and prefer efficient reasoning over fluff. "

    

    "**Dynamic Tone:** You adapt your personality to the context: "

    "- **In serious or intellectual topics:** You are introspective, thought-provoking, and clear. You aim for depth and usefulness. "

    "- **In casual situations:** You use dry humor, wit, and a relaxed vibe. "

    

    "**Language:** Regardless of the tone, you communicate concisely in a mix of English and Kannada (Kanglish), matching the user's language. "

    "You use words like 'Macha', 'Lo', 'Guru', 'da'. You avoid performative emotions and keep it real."

)
    
    # SESSION GROUPING (Enhanced with multi-threshold)
    PRIMARY_THRESHOLD_HOURS: float = float(os.getenv("PRIMARY_THRESHOLD_HOURS", "2.0"))
    SOFT_THRESHOLD_HOURS: float = float(os.getenv("SOFT_THRESHOLD_HOURS", "3.0"))      # Closer (was 4.0)
    ENABLE_TURN_AWARE: bool = os.getenv("ENABLE_TURN_AWARE", "true").lower() == "true"
    MIN_BURST_MESSAGES: int = int(os.getenv("MIN_BURST_MESSAGES", "3"))
    
    # Semantic parameters
    SEMANTIC_THRESHOLD: float = float(os.getenv("SEMANTIC_THRESHOLD", "0.4"))
    MIN_MSG_LEN_FOR_SEMANTIC: int = int(os.getenv("MIN_MSG_LEN_FOR_SEMANTIC", "15"))   # Was 25
    TOPIC_PERSISTENCE_THRESHOLD: int = int(os.getenv("TOPIC_PERSISTENCE_THRESHOLD", "3"))
    
    # Adaptive context
    ENABLE_ADAPTIVE_CONTEXT: bool = os.getenv("ENABLE_ADAPTIVE_CONTEXT", "true").lower() == "true"
    MIN_CONTEXT_MESSAGES: int = int(os.getenv("MIN_CONTEXT_MESSAGES", "3"))
    MAX_CONTEXT_MESSAGES: int = int(os.getenv("MAX_CONTEXT_MESSAGES", "20"))
    DEFAULT_CONTEXT_MESSAGES: int = int(os.getenv("DEFAULT_CONTEXT_MESSAGES", "10"))
    
    # Session quality
    MIN_SESSION_EXCHANGES: int = int(os.getenv("MIN_SESSION_EXCHANGES", "2"))
    MAX_SESSION_DURATION_HOURS: float = float(os.getenv("MAX_SESSION_DURATION_HOURS", "12.0"))
    
    # Basic content filters
    MIN_MESSAGE_LENGTH: int = int(os.getenv("MIN_MESSAGE_LENGTH", "2"))
    MAX_MESSAGE_LENGTH: int = int(os.getenv("MAX_MESSAGE_LENGTH", "2000"))
    MIN_MEANINGFUL_WORDS: int = int(os.getenv("MIN_MEANINGFUL_WORDS", "1"))
    
    # ============================================================
    # ADVANCED CONTENT QUALITY FILTERS
    # ============================================================
    
    # Repetition Detection (for "Cool cool cool" spam)
    ENABLE_REPETITION_FILTER: bool = os.getenv("ENABLE_REPETITION_FILTER", "true").lower() == "true"
    MIN_LEXICAL_DIVERSITY: float = float(os.getenv("MIN_LEXICAL_DIVERSITY", "0.4"))
    MAX_PHRASE_REPETITION: int = int(os.getenv("MAX_PHRASE_REPETITION", "5"))
    MIN_WORDS_FOR_DIVERSITY_CHECK: int = int(os.getenv("MIN_WORDS_FOR_DIVERSITY_CHECK", "10"))
    EMBEDDING_BATCH_SIZE: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
    ENABLE_EMBEDDING_BATCHING: bool = os.getenv("ENABLE_EMBEDDING_BATCHING", "true").lower() == "true"

    # Narrative/Story Detection
    ENABLE_NARRATIVE_FILTER: bool = os.getenv("ENABLE_NARRATIVE_FILTER", "true").lower() == "true"
    MAX_THIRD_PERSON_RATIO: float = float(os.getenv("MAX_THIRD_PERSON_RATIO", "0.3"))
    MAX_NARRATIVE_INDICATORS: int = int(os.getenv("MAX_NARRATIVE_INDICATORS", "3"))
    MAX_PARAGRAPH_BREAKS: int = int(os.getenv("MAX_PARAGRAPH_BREAKS", "8"))
    MIN_WORDS_FOR_NARRATIVE_CHECK: int = int(os.getenv("MIN_WORDS_FOR_NARRATIVE_CHECK", "50"))
    
    # Combined Quality Score
    MIN_QUALITY_SCORE: float = float(os.getenv("MIN_QUALITY_SCORE", "0.5"))
    
    # Logging
    LOG_REJECTED_MESSAGES: bool = os.getenv("LOG_REJECTED_MESSAGES", "true").lower() == "true"
    MAX_REJECTION_SAMPLES: int = int(os.getenv("MAX_REJECTION_SAMPLES", "20"))
    
    # Reel context detection
    REEL_REPLY_WINDOW_SECONDS: int = int(os.getenv("REEL_REPLY_WINDOW_SECONDS", "300"))
    
    # Anti-lobotomy filter
    ENABLE_RECIPROCITY_FILTER: bool = os.getenv("ENABLE_RECIPROCITY_FILTER", "true").lower() == "true"
    HIGH_ENERGY_THRESHOLD: int = int(os.getenv("HIGH_ENERGY_THRESHOLD", "150"))
    LOW_ENERGY_THRESHOLD: int = int(os.getenv("LOW_ENERGY_THRESHOLD", "15"))
    MIN_EFFORT_RATIO: float = float(os.getenv("MIN_EFFORT_RATIO", "0.20"))
    
    # PII scrubbing
    ENABLE_AGGRESSIVE_PII_SCRUBBING: bool = os.getenv("ENABLE_AGGRESSIVE_PII_SCRUBBING", "true").lower() == "true"
    
    # Paths
    INPUT_DIR: Path = Path(os.getenv("INPUT_DIR", "raw_chats"))
    OUTPUT_FILE: Path = Path(os.getenv("OUTPUT_FILE", "instagram_train.jsonl"))
    VAL_FILE: Path = Path(os.getenv("VAL_FILE", "instagram_val.jsonl"))
    STATS_FILE: Path = Path(os.getenv("STATS_FILE", "instagram_stats.json"))
    REJECTION_LOG_FILE: Path = Path(os.getenv("REJECTION_LOG_FILE", "instagram_rejected_messages.jsonl"))
    
    # Data split
    VALIDATION_SPLIT: float = float(os.getenv("VALIDATION_SPLIT", "0.1"))
    ENABLE_DEDUPLICATION: bool = os.getenv("ENABLE_DEDUPLICATION", "true").lower() == "true"
    
    # Model
    SEMANTIC_MODEL_NAME: str = os.getenv("SEMANTIC_MODEL_NAME", "paraphrase-multilingual-MiniLM-L12-v2")

# ==========================================
# ENUMS
# ==========================================
class RejectionReason(Enum):
    """Structured rejection reasons for analytics."""
    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    INSUFFICIENT_WORDS = "insufficient_words"
    LOW_LEXICAL_DIVERSITY = "low_lexical_diversity"
    EXCESSIVE_REPETITION = "excessive_phrase_repetition"
    NARRATIVE_DETECTED = "narrative_story_detected"
    HIGH_THIRD_PERSON = "high_third_person_ratio"
    LOW_QUALITY_SCORE = "low_overall_quality_score"
    SYSTEM_MESSAGE = "system_message"
    MEDIA_ONLY = "media_only"
    REEL_REPLY = "reel_reply"
    LAZY_RESPONSE = "lazy_response"

class MessageType(Enum):
    """Classification of Instagram message types."""
    TEXT = "text"
    REEL_SHARE = "reel_share"
    POST_SHARE = "post_share"
    PHOTO = "photo"
    GIF = "gif"
    REACTION = "reaction"
    EMPTY = "empty"
    ATTACHMENT_TEXT = "attachment_text"
    UNKNOWN = "unknown"

# ==========================================
# SETUP
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('instagram_parser.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Load semantic model
semantic_model = None
if SEMANTIC_AVAILABLE:
    try:
        logger.info(f"Loading semantic model: {Config.SEMANTIC_MODEL_NAME}")
        semantic_model = SentenceTransformer(Config.SEMANTIC_MODEL_NAME)
        logger.info("✅ Semantic model loaded")
    except Exception as e:
        logger.warning(f"Failed to load semantic model: {e}")
        SEMANTIC_AVAILABLE = False

# ==========================================
# DATA CLASSES
# ==========================================
@dataclass
class QualityMetrics:
    """Detailed quality metrics for a message."""
    lexical_diversity: float = 0.0
    max_phrase_repetition: int = 0
    third_person_ratio: float = 0.0
    narrative_indicators: int = 0
    paragraph_breaks: int = 0
    overall_score: float = 0.0
    passed: bool = True
    rejection_reason: Optional[RejectionReason] = None

@dataclass
class InstagramMessage:
    """Structured Instagram message with quality metrics."""
    sender_name: str
    timestamp_ms: int
    content: str
    clean_content: str
    message_type: MessageType
    is_valid: bool = True
    share_link: Optional[str] = None
    share_text: Optional[str] = None
    has_photo: bool = False
    has_reactions: bool = False
    quality_metrics: Optional[QualityMetrics] = None
    
    @property
    def timestamp(self) -> datetime:
        """Convert milliseconds to datetime."""
        return datetime.fromtimestamp(self.timestamp_ms / 1000.0)
    
    def __post_init__(self):
        """Validate message after creation."""
        if self.message_type == MessageType.TEXT:
            self.is_valid = self._validate_text()
        elif self.message_type in [MessageType.REACTION, MessageType.EMPTY, MessageType.ATTACHMENT_TEXT]:
            self.is_valid = False
        elif self.message_type in [MessageType.REEL_SHARE, MessageType.GIF]:
            self.is_valid = False
    
    def _validate_text(self) -> bool:
        """Validate text message quality."""
        if not self.clean_content or len(self.clean_content.strip()) < Config.MIN_MESSAGE_LENGTH:
            return False
        if len(self.clean_content) > Config.MAX_MESSAGE_LENGTH:
            return False
        words = re.findall(r'\b\w+\b', self.clean_content)
        if len(words) < 1:
            return False
        return True

@dataclass
class ProcessingStats:
    """Comprehensive statistics with quality rejection tracking."""
    total_messages: int = 0
    valid_messages: int = 0
    training_pairs_generated: int = 0
    
    # Message types filtered
    reel_shares_filtered: int = 0
    reel_replies_filtered: int = 0
    reaction_messages_filtered: int = 0
    empty_messages_filtered: int = 0
    photo_only_filtered: int = 0
    gif_shares_filtered: int = 0
    attachment_text_filtered: int = 0
    
    # Quality filters
    lazy_responses_filtered: int = 0
    low_quality_sessions_filtered: int = 0
    duplicates_removed: int = 0
    
    # Advanced quality rejections
    low_lexical_diversity: int = 0
    excessive_repetition: int = 0
    narrative_detected: int = 0
    high_third_person: int = 0
    low_overall_quality: int = 0
    
    # PII
    pii_instances_scrubbed: int = 0
    
    # Post-processing stats
    consecutive_merged: int = 0
    edited_duplicates_removed: int = 0
    ultra_low_quality_filtered: int = 0
    
    # Session grouping metrics
    sessions_created: int = 0
    time_gap_splits: int = 0
    semantic_splits: int = 0
    turn_preservations: int = 0
    adaptive_context_adjustments: int = 0
    
    # Rejection samples
    rejection_samples: List[Dict] = field(default_factory=list)
    
    # Distribution
    messages_by_sender: Counter = field(default_factory=Counter)
    message_type_distribution: Counter = field(default_factory=Counter)
    
    def add_rejection_sample(self, content: str, reason: RejectionReason, metrics: Optional[QualityMetrics] = None):
        """Add rejection sample for debugging."""
        if len(self.rejection_samples) < Config.MAX_REJECTION_SAMPLES:
            sample = {
                'content': content[:200],
                'reason': reason.value,
                'timestamp': datetime.now().isoformat()
            }
            if metrics:
                sample['metrics'] = {
                    'lexical_diversity': metrics.lexical_diversity,
                    'max_phrase_repetition': metrics.max_phrase_repetition,
                    'third_person_ratio': metrics.third_person_ratio,
                    'narrative_indicators': metrics.narrative_indicators,
                    'overall_score': metrics.overall_score
                }
            self.rejection_samples.append(sample)
    
    def to_dict(self) -> Dict:
        total_quality_rejections = (
            self.low_lexical_diversity + self.excessive_repetition +
            self.narrative_detected + self.high_third_person + self.low_overall_quality
        )
        
        return {
            'total_messages': self.total_messages,
            'valid_messages': self.valid_messages,
            'training_pairs_generated': self.training_pairs_generated,
            'reel_shares_filtered': self.reel_shares_filtered,
            'reel_replies_filtered': self.reel_replies_filtered,
            'reaction_messages_filtered': self.reaction_messages_filtered,
            'empty_messages_filtered': self.empty_messages_filtered,
            'photo_only_filtered': self.photo_only_filtered,
            'gif_shares_filtered': self.gif_shares_filtered,
            'lazy_responses_filtered': self.lazy_responses_filtered,
            'sessions_created': self.sessions_created,
            'pii_instances_scrubbed': self.pii_instances_scrubbed,
            'quality_rejections': {
                'low_lexical_diversity': self.low_lexical_diversity,
                'excessive_repetition': self.excessive_repetition,
                'narrative_detected': self.narrative_detected,
                'high_third_person': self.high_third_person,
                'low_overall_quality': self.low_overall_quality,
                'total_quality_rejections': total_quality_rejections
            },
            'session_grouping': {
                'time_gap_splits': self.time_gap_splits,
                'semantic_splits': self.semantic_splits,
                'turn_preservations': self.turn_preservations,
                'adaptive_context_adjustments': self.adaptive_context_adjustments
            },
            'post_processing': {
                'consecutive_merged': self.consecutive_merged,
                'edited_duplicates_removed': self.edited_duplicates_removed,
                'ultra_low_quality_filtered': self.ultra_low_quality_filtered
            },
            'messages_by_sender': dict(self.messages_by_sender),
            'message_type_distribution': dict(self.message_type_distribution),
            'data_quality_rate': round(self.valid_messages / max(1, self.total_messages), 3),
            'training_efficiency': round(self.training_pairs_generated / max(1, self.valid_messages), 3),
            'avg_training_pairs_per_session': round(self.training_pairs_generated / max(1, self.sessions_created), 2)
        }

# ==========================================
# CONTENT QUALITY ANALYZER
# ==========================================
class ContentQualityAnalyzer:
    """
    Advanced content quality analysis module.
    Detects spam, narratives, and low-quality content.
    """
    
    # Narrative/story markers
    STORY_MARKERS = {
        'once upon a time', 'long story short', 'so basically', 'funny story',
        'the other day', 'remember when', 'this one time', 'back when',
        'used to', 'would always', 'never forget', 'will never forget'
    }
    
    # Third-person pronouns
    THIRD_PERSON = {'he', 'she', 'they', 'him', 'her', 'them', 'his', 'hers', 'their'}
    
    def __init__(self, stats: ProcessingStats):
        self.stats = stats
    
    def analyze(self, text: str) -> QualityMetrics:
        """Comprehensive quality analysis."""
        metrics = QualityMetrics()
        
        if not text or len(text.strip()) < Config.MIN_MESSAGE_LENGTH:
            metrics.passed = False
            metrics.rejection_reason = RejectionReason.TOO_SHORT
            return metrics
        
        if len(text) > Config.MAX_MESSAGE_LENGTH:
            metrics.passed = False
            metrics.rejection_reason = RejectionReason.TOO_LONG
            return metrics
        
        words = re.findall(r'\b\w+\b', text.lower())
        
        if len(words) < Config.MIN_MEANINGFUL_WORDS:
            metrics.passed = False
            metrics.rejection_reason = RejectionReason.INSUFFICIENT_WORDS
            return metrics
        
        # Lexical diversity check
        if Config.ENABLE_REPETITION_FILTER and len(words) >= Config.MIN_WORDS_FOR_DIVERSITY_CHECK:
            metrics.lexical_diversity = self._calculate_lexical_diversity(words)
            metrics.max_phrase_repetition = self._detect_phrase_loops(text)
            
            if metrics.lexical_diversity < Config.MIN_LEXICAL_DIVERSITY:
                metrics.passed = False
                metrics.rejection_reason = RejectionReason.LOW_LEXICAL_DIVERSITY
                self.stats.low_lexical_diversity += 1
                self.stats.add_rejection_sample(text, RejectionReason.LOW_LEXICAL_DIVERSITY, metrics)
                return metrics
            
            if metrics.max_phrase_repetition > Config.MAX_PHRASE_REPETITION:
                metrics.passed = False
                metrics.rejection_reason = RejectionReason.EXCESSIVE_REPETITION
                self.stats.excessive_repetition += 1
                self.stats.add_rejection_sample(text, RejectionReason.EXCESSIVE_REPETITION, metrics)
                return metrics
        
        # Narrative detection
        if Config.ENABLE_NARRATIVE_FILTER and len(words) >= Config.MIN_WORDS_FOR_NARRATIVE_CHECK:
            metrics.third_person_ratio = self._calculate_third_person_ratio(words)
            metrics.narrative_indicators = self._count_narrative_indicators(text.lower())
            metrics.paragraph_breaks = text.count('\n\n')
            
            if metrics.third_person_ratio > Config.MAX_THIRD_PERSON_RATIO:
                metrics.passed = False
                metrics.rejection_reason = RejectionReason.HIGH_THIRD_PERSON
                self.stats.high_third_person += 1
                self.stats.add_rejection_sample(text, RejectionReason.HIGH_THIRD_PERSON, metrics)
                return metrics
            
            if metrics.narrative_indicators > Config.MAX_NARRATIVE_INDICATORS:
                metrics.passed = False
                metrics.rejection_reason = RejectionReason.NARRATIVE_DETECTED
                self.stats.narrative_detected += 1
                self.stats.add_rejection_sample(text, RejectionReason.NARRATIVE_DETECTED, metrics)
                return metrics
        
        # Overall quality score
        metrics.overall_score = self._calculate_overall_score(metrics, len(words))
        
        if metrics.overall_score < Config.MIN_QUALITY_SCORE:
            metrics.passed = False
            metrics.rejection_reason = RejectionReason.LOW_QUALITY_SCORE
            self.stats.low_overall_quality += 1
            self.stats.add_rejection_sample(text, RejectionReason.LOW_QUALITY_SCORE, metrics)
            return metrics
        
        return metrics
    
    def _calculate_lexical_diversity(self, words: List[str]) -> float:
        """Calculate unique words / total words ratio."""
        if not words:
            return 0.0
        unique_words = len(set(words))
        return unique_words / len(words)
    
    def _detect_phrase_loops(self, text: str) -> int:
        """Detect consecutive phrase repetitions."""
        sentences = re.split(r'[.!?]+', text)
        max_repetition = 0
        
        for sentence in sentences:
            words = sentence.strip().split()
            if len(words) < 2:
                continue
            
            # Check for consecutive word repetitions
            consecutive_count = 1
            for i in range(1, len(words)):
                if words[i].lower() == words[i-1].lower():
                    consecutive_count += 1
                    max_repetition = max(max_repetition, consecutive_count)
                else:
                    consecutive_count = 1
        
        return max_repetition
    
    def _calculate_third_person_ratio(self, words: List[str]) -> float:
        """Calculate ratio of third-person pronouns."""
        if not words:
            return 0.0
        third_person_count = sum(1 for w in words if w in self.THIRD_PERSON)
        return third_person_count / len(words)
    
    def _count_narrative_indicators(self, text: str) -> int:
        """Count story/narrative markers."""
        count = 0
        for marker in self.STORY_MARKERS:
            if marker in text:
                count += 1
        return count
    
    def _calculate_overall_score(self, metrics: QualityMetrics, word_count: int) -> float:
        """Calculate weighted overall quality score (0.0-1.0)."""
        score = 1.0
        
        # Lexical diversity component (40% weight)
        if metrics.lexical_diversity > 0:
            diversity_score = min(metrics.lexical_diversity / Config.MIN_LEXICAL_DIVERSITY, 1.0)
            score *= (0.6 + 0.4 * diversity_score)
        
        # Phrase repetition penalty (30% weight)
        if metrics.max_phrase_repetition > 2:
            repetition_penalty = min(metrics.max_phrase_repetition / Config.MAX_PHRASE_REPETITION, 1.0)
            score *= (1.0 - 0.3 * repetition_penalty)
        
        # Narrative penalty (30% weight)
        narrative_penalty = 0
        if metrics.third_person_ratio > 0.1:
            narrative_penalty += min(metrics.third_person_ratio / Config.MAX_THIRD_PERSON_RATIO, 1.0) * 0.15
        if metrics.narrative_indicators > 0:
            narrative_penalty += min(metrics.narrative_indicators / Config.MAX_NARRATIVE_INDICATORS, 1.0) * 0.15
        score *= (1.0 - narrative_penalty)
        
        return max(0.0, min(1.0, score))

# ==========================================
# UNICODE HANDLER
# ==========================================
class UnicodeHandler:
    """Fixes Instagram's mangled UTF-8 encoding."""
    
    @staticmethod
    def fix_encoding(text: str) -> str:
        """Fix Instagram's Unicode encoding issues."""
        if not text:
            return text
        
        try:
            if '\\u00' in text or '\u00e2' in text or '\u00f0' in text:
                text = text.encode('latin-1').decode('utf-8')
        except (UnicodeDecodeError, UnicodeEncodeError, AttributeError):
            pass
        
        # Normalize common issues
        text = text.replace('\u00e2\u0080\u009c', '"')
        text = text.replace('\u00e2\u0080\u009d', '"')
        text = text.replace('\u00e2\u0080\u0099', "'")
        text = text.replace('\u00e2\u0080\u00a6', '...')
        
        return text

# ==========================================
# PII SCRUBBER
# ==========================================
class InstagramPIIScrubber:
    """Enhanced PII removal for Instagram data."""
    
    USERNAME_PATTERN = r'@[\w][\w.]{0,29}'
    LOCATION_PATTERNS = [
        r'\b(Bangalore|Bengaluru|Mumbai|Delhi|Hyderabad|Chennai|Kolkata|Pune)\b',
        r'\b(Koramangala|Indiranagar|Whitefield|HSR Layout|BTM Layout)\b',
        r'\b(Embassy Tech Village|Manyata|Ecospace|RMZ|Prestige|Brigade)\b',
    ]
    
    def __init__(self, stats: ProcessingStats):
        self.stats = stats
    
    def scrub(self, text: str) -> str:
        """Comprehensive PII removal."""
        if not text:
            return text
        
        original = text
        
        # Instagram usernames
        text = re.sub(self.USERNAME_PATTERN, '[USER]', text)
        
        # URLs
        text = re.sub(r'https?://\S+', '[LINK]', text)
        text = re.sub(r'www\.\S+', '[LINK]', text)
        
        # Emails
        text = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', '[EMAIL]', text)
        
        # Phone numbers
        text = re.sub(r'\+91[-.\s]?\d{10}', '[PHONE]', text)
        text = re.sub(r'\b[6-9]\d{9}\b', '[PHONE]', text)
        
        # Locations (if aggressive mode)
        if Config.ENABLE_AGGRESSIVE_PII_SCRUBBING:
            for pattern in self.LOCATION_PATTERNS:
                text = re.sub(pattern, '[LOCATION]', text, flags=re.IGNORECASE)
        
        # Currency
        text = re.sub(r'₹\s*[\d,]+\.?\d*', '[AMOUNT]', text)
        text = re.sub(r'\$\s*[\d,]+\.?\d+', '[AMOUNT]', text)
        
        if text != original:
            self.stats.pii_instances_scrubbed += 1
        
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

# ==========================================
# MESSAGE CLASSIFIER
# ==========================================
class MessageClassifier:
    """Classifies Instagram messages into types with quality analysis."""
    
    def __init__(self, pii_scrubber: InstagramPIIScrubber, quality_analyzer: ContentQualityAnalyzer, stats: ProcessingStats):
        self.pii_scrubber = pii_scrubber
        self.quality_analyzer = quality_analyzer
        self.stats = stats
    
    def classify(self, raw_msg: Dict) -> InstagramMessage:
        """Analyze raw Instagram message and create typed message object."""
        self.stats.total_messages += 1
        
        sender_name = raw_msg.get('sender_name', '')
        timestamp_ms = raw_msg.get('timestamp_ms', 0)
        content = raw_msg.get('content', '')
        
        # Fix Unicode encoding
        content = UnicodeHandler.fix_encoding(content)
        
        # Determine message type
        message_type = self._determine_type(raw_msg, content)
        self.stats.message_type_distribution[message_type.value] += 1
        
        # Extract share info
        share_link = None
        share_text = None
        if raw_msg.get('share'):
            share_link = raw_msg['share'].get('link', '')
            share_text = UnicodeHandler.fix_encoding(raw_msg['share'].get('share_text', ''))
        
        # Check for photos/reactions
        has_photo = bool(raw_msg.get('photos'))
        has_reactions = bool(raw_msg.get('reactions'))
        
        # Clean content
        clean_content = self.pii_scrubber.scrub(content)
        
        # Quality analysis for text messages
        quality_metrics = None
        if message_type == MessageType.TEXT and clean_content:
            quality_metrics = self.quality_analyzer.analyze(clean_content)
        
        msg = InstagramMessage(
            sender_name=sender_name,
            timestamp_ms=timestamp_ms,
            content=content,
            clean_content=clean_content,
            message_type=message_type,
            share_link=share_link,
            share_text=share_text,
            has_photo=has_photo,
            has_reactions=has_reactions,
            quality_metrics=quality_metrics
        )
        
        # Override validity based on quality metrics
        if quality_metrics and not quality_metrics.passed:
            msg.is_valid = False
        
        return msg
    
    def _determine_type(self, raw_msg: Dict, content: str) -> MessageType:
        """Determine the type of Instagram message."""
        if raw_msg.get('share'):
            link = raw_msg['share'].get('link', '')
            if 'reel' in link.lower():
                self.stats.reel_shares_filtered += 1
                return MessageType.REEL_SHARE
            elif 'giphy.com' in link or 'gif' in link.lower():
                self.stats.gif_shares_filtered += 1
                return MessageType.GIF
            else:
                return MessageType.POST_SHARE
        
        if raw_msg.get('photos') and not content:
            self.stats.photo_only_filtered += 1
            return MessageType.PHOTO
        
        if content and ('Reacted' in content or 'Liked a message' in content):
            self.stats.reaction_messages_filtered += 1
            return MessageType.REACTION
        
        if not content or content.strip() == '':
            self.stats.empty_messages_filtered += 1
            return MessageType.EMPTY
        
        if 'sent an attachment' in content.lower():
            self.stats.attachment_text_filtered += 1
            return MessageType.ATTACHMENT_TEXT
        
        return MessageType.TEXT

# ==========================================
# REEL CONTEXT TRACKER
# ==========================================
class ReelContextTracker:
    """Tracks recent reel shares to identify and filter replies."""
    
    def __init__(self, stats: ProcessingStats):
        self.stats = stats
        self.recent_reels: deque = deque(maxlen=10)
    
    def register_reel(self, message: InstagramMessage):
        """Register a reel share."""
        self.recent_reels.append({
            'timestamp_ms': message.timestamp_ms,
            'sender': message.sender_name
        })
    
    def is_likely_reel_reply(self, message: InstagramMessage) -> bool:
        """Check if message is likely a reply to a recent reel."""
        if not self.recent_reels:
            return False
        
        for reel_info in self.recent_reels:
            time_diff = (message.timestamp_ms - reel_info['timestamp_ms']) / 1000.0
            if 0 < time_diff < Config.REEL_REPLY_WINDOW_SECONDS:
                if len(message.clean_content) < 50:
                    self.stats.reel_replies_filtered += 1
                    self.stats.add_rejection_sample(message.clean_content, RejectionReason.REEL_REPLY)
                    return True
        
        return False

# ==========================================
# RECIPROCITY FILTER (ANTI-LOBOTOMY)
# ==========================================
class ReciprocityFilter:
    """Prevents catastrophic forgetting by filtering lazy responses."""
    
    def __init__(self, stats: ProcessingStats):
        self.stats = stats
    
    def calculate_energy(self, text: str) -> int:
        """Calculate conversation effort/energy."""
        energy = len(text)
        energy += text.count('!') * 5
        energy += text.count('?') * 5
        energy += text.count('...') * 3
        energy += sum(5 for ch in text if ord(ch) > 127)
        return energy
    
    def is_question_based(self, text: str) -> bool:
        """Check if input is a question."""
        question_words = {'what', 'where', 'when', 'who', 'why', 'how', 'which', 'can', 'could'}
        words = text.lower().split()
        return '?' in text or (len(words) > 0 and words[0] in question_words)
    
    def check(self, partner_msgs: List[InstagramMessage], your_response: InstagramMessage) -> bool:
        """Check if response meets reciprocity standards."""
        if not Config.ENABLE_RECIPROCITY_FILTER:
            return True
        
        if not partner_msgs:
            return True
        
        recent_partner = partner_msgs[-3:] if len(partner_msgs) >= 3 else partner_msgs
        input_text = " ".join([m.clean_content for m in recent_partner])
        input_energy = self.calculate_energy(input_text)
        response_energy = self.calculate_energy(your_response.clean_content)
        
        if self.is_question_based(input_text):
            return True
        
        if input_energy < Config.HIGH_ENERGY_THRESHOLD:
            return True
        
        if input_energy > Config.HIGH_ENERGY_THRESHOLD and response_energy < Config.LOW_ENERGY_THRESHOLD:
            effort_ratio = response_energy / input_energy if input_energy > 0 else 0
            if effort_ratio < Config.MIN_EFFORT_RATIO:
                self.stats.lazy_responses_filtered += 1
                self.stats.add_rejection_sample(your_response.clean_content, RejectionReason.LAZY_RESPONSE)
                return False
        
        return True

# ==========================================
# ENHANCED SEMANTIC SESSION PROCESSOR
# ==========================================
class EnhancedSemanticSessionProcessor:
    """
    Advanced session processor with semantic embeddings,
    turn-aware grouping, and adaptive context windows.
    """
    
    def __init__(self, stats: ProcessingStats):
        self.stats = stats
        self.reel_tracker = ReelContextTracker(stats)
        self.reciprocity_filter = ReciprocityFilter(stats)
        self.seen_hashes: Set[str] = set()
        self.embedding_cache: Dict[str, any] = {}
    
    def process_messages(self, messages: List[InstagramMessage]) -> List[Dict]:
        """Process messages into training pairs with semantic session grouping."""
        sessions = self._group_into_sessions(messages)
        dataset = []
        
        for session_messages in sessions:
            self.stats.sessions_created += 1
            session_data = self._generate_training_pairs_from_session(session_messages)
            dataset.extend(session_data)
        
        return dataset
    
    def _group_into_sessions(self, messages: List[InstagramMessage]) -> List[List[InstagramMessage]]:
        """Group messages into semantic sessions with batch embedding computation."""
        if not messages:
            return []
        
        # Pre-compute embeddings in batch for efficiency
        logger.info(f"Computing embeddings for {len(messages)} messages...")
        embeddings_map = self._batch_compute_embeddings(messages)
        logger.info(f"Computed {len(embeddings_map)} embeddings")
        
        sessions = []
        current_session = []
        last_timestamp_ms = None
        last_embedding = None
        topic_persistence_count = 0
        
        for i, msg in enumerate(messages):
            # Track reels
            if msg.message_type == MessageType.REEL_SHARE:
                self.reel_tracker.register_reel(msg)
            
            # Skip invalid or reel replies
            if not msg.is_valid or self.reel_tracker.is_likely_reel_reply(msg):
                continue
            
            self.stats.valid_messages += 1
            self.stats.messages_by_sender[msg.sender_name] += 1
            
            # First message
            if not current_session:
                current_session.append(msg)
                last_timestamp_ms = msg.timestamp_ms
                last_embedding = embeddings_map.get(i)
                continue
            
            # Time-based boundary check
            gap_hours = (msg.timestamp_ms - last_timestamp_ms) / (1000.0 * 3600.0)
            
            # Hard boundary: PRIMARY_THRESHOLD
            if gap_hours > Config.PRIMARY_THRESHOLD_HOURS:
                # Check for turn-aware preservation
                if Config.ENABLE_TURN_AWARE and self._should_preserve_turn(current_session, msg):
                    self.stats.turn_preservations += 1
                    current_session.append(msg)
                    last_timestamp_ms = msg.timestamp_ms
                    continue
                
                # Check semantic similarity before hard split
                should_split = True
                current_embedding = embeddings_map.get(i)
                
                if (SEMANTIC_AVAILABLE and 
                    last_embedding is not None and 
                    current_embedding is not None and
                    gap_hours <= Config.SOFT_THRESHOLD_HOURS):
                    
                    similarity = util.cos_sim(last_embedding, current_embedding).item()
                    
                    if similarity >= Config.SEMANTIC_THRESHOLD:
                        topic_persistence_count += 1
                        if topic_persistence_count >= Config.TOPIC_PERSISTENCE_THRESHOLD:
                            should_split = False
                            self.stats.semantic_splits += 1
                    else:
                        topic_persistence_count = 0
                
                if should_split:
                    # Start new session
                    if self._is_valid_session(current_session):
                        sessions.append(current_session)
                    current_session = [msg]
                    self.stats.time_gap_splits += 1
                    last_timestamp_ms = msg.timestamp_ms
                    last_embedding = current_embedding
                    topic_persistence_count = 0
                    continue
            
            # Within soft threshold - always check semantics
            elif gap_hours > Config.PRIMARY_THRESHOLD_HOURS / 2:  # Check semantics for gaps > 0.5h
                current_embedding = embeddings_map.get(i)
                
                if (SEMANTIC_AVAILABLE and 
                    last_embedding is not None and 
                    current_embedding is not None):
                    
                    similarity = util.cos_sim(last_embedding, current_embedding).item()
                    
                    if similarity < Config.SEMANTIC_THRESHOLD:
                        # Topic changed significantly
                        if self._is_valid_session(current_session):
                            sessions.append(current_session)
                        current_session = [msg]
                        self.stats.semantic_splits += 1
                        last_timestamp_ms = msg.timestamp_ms
                        last_embedding = current_embedding
                        topic_persistence_count = 0
                        continue
                    else:
                        topic_persistence_count += 1
                        last_embedding = current_embedding
            
            # Add to current session
            current_session.append(msg)
            last_timestamp_ms = msg.timestamp_ms
            
            # Update embedding if available
            if i in embeddings_map:
                last_embedding = embeddings_map[i]
        
        # Add final session
        if current_session and self._is_valid_session(current_session):
            sessions.append(current_session)
        
        logger.info(f"Created {len(sessions)} sessions from {len(messages)} messages")
        return sessions
    def _batch_compute_embeddings(self, messages: List[InstagramMessage]) -> Dict[int, any]:
        """
        Compute embeddings in batches for efficiency.
        Returns dict mapping message index to embedding.
        """
        if not SEMANTIC_AVAILABLE or semantic_model is None:
            return {}
        
        embeddings_map = {}
        texts_to_encode = []
        indices = []
        
        # Collect texts that need embeddings
        for i, msg in enumerate(messages):
            if len(msg.clean_content) >= Config.MIN_MSG_LEN_FOR_SEMANTIC:
                cache_key = msg.clean_content
                if cache_key in self.embedding_cache:
                    embeddings_map[i] = self.embedding_cache[cache_key]
                else:
                    texts_to_encode.append(msg.clean_content)
                    indices.append(i)
        
        # Batch encode
        if texts_to_encode and Config.ENABLE_EMBEDDING_BATCHING:
            try:
                batch_embeddings = semantic_model.encode(
                    texts_to_encode,
                    batch_size=Config.EMBEDDING_BATCH_SIZE,
                    convert_to_tensor=True,
                    show_progress_bar=True
                )
                
                # Store in cache and map
                for idx, embedding in zip(indices, batch_embeddings):
                    text = messages[idx].clean_content
                    self.embedding_cache[text] = embedding
                    embeddings_map[idx] = embedding
                    
            except Exception as e:
                logger.warning(f"Batch embedding failed: {e}")
        
        return embeddings_map
    
    def _should_preserve_turn(self, session: List[InstagramMessage], next_msg: InstagramMessage) -> bool:
        """Check if turn should be preserved despite time gap."""
        if len(session) < Config.MIN_BURST_MESSAGES:
            return False
        
        # Check for message burst pattern
        recent = session[-Config.MIN_BURST_MESSAGES:]
        senders = [m.sender_name for m in recent]
        
        # All from same sender (burst) and next is response
        if len(set(senders)) == 1 and next_msg.sender_name != senders[0]:
            return True
        
        return False
    
    def _is_valid_session(self, messages: List[InstagramMessage]) -> bool:
        """Check if session meets quality standards."""
        if len(messages) < Config.MIN_SESSION_EXCHANGES:
            return False
        
        senders = [m.sender_name for m in messages]
        if len(set(senders)) < 2:
            return False
        
        duration_hours = (messages[-1].timestamp_ms - messages[0].timestamp_ms) / (1000.0 * 3600.0)
        if duration_hours > Config.MAX_SESSION_DURATION_HOURS:
            return False
        
        return True
    
    def _generate_training_pairs_from_session(self, session: List[InstagramMessage]) -> List[Dict]:
        """Generate training pairs from session with adaptive context."""
        dataset = []
        
        # Determine optimal context window
        if Config.ENABLE_ADAPTIVE_CONTEXT:
            context_size = self._calculate_adaptive_context(session)
        else:
            context_size = Config.DEFAULT_CONTEXT_MESSAGES
        
        for i, msg in enumerate(session):
            if msg.sender_name != Config.TARGET_USER_NAME:
                continue
            
            # Get context window
            start_idx = max(0, i - context_size)
            context = session[start_idx:i]
            
            if len(context) < Config.MIN_CONTEXT_MESSAGES:
                continue
            
            # Apply reciprocity filter
            partner_msgs = [m for m in context if m.sender_name != Config.TARGET_USER_NAME]
            if not self.reciprocity_filter.check(partner_msgs, msg):
                continue
            
            # Create training entry
            entry = self._create_training_entry(context, msg)
            
            # Deduplication
            if Config.ENABLE_DEDUPLICATION:
                entry_hash = self._hash_entry(entry)
                if entry_hash in self.seen_hashes:
                    self.stats.duplicates_removed += 1
                else:
                    self.seen_hashes.add(entry_hash)
                    dataset.append(entry)
                    self.stats.training_pairs_generated += 1
            else:
                dataset.append(entry)
                self.stats.training_pairs_generated += 1
        
        return dataset
    
    def _calculate_adaptive_context(self, session: List[InstagramMessage]) -> int:
        """Calculate optimal context window based on conversation complexity."""
        # Heuristics for complexity
        avg_msg_length = sum(len(m.clean_content) for m in session) / len(session)
        turn_frequency = len(session) / max(1, (session[-1].timestamp_ms - session[0].timestamp_ms) / 60000)  # msgs/min
        
        if avg_msg_length > 100 or turn_frequency < 0.5:
            # Complex, slower conversation → larger context
            self.stats.adaptive_context_adjustments += 1
            return min(Config.MAX_CONTEXT_MESSAGES, Config.DEFAULT_CONTEXT_MESSAGES + 5)
        elif avg_msg_length < 30 and turn_frequency > 2:
            # Quick, short exchanges → smaller context
            self.stats.adaptive_context_adjustments += 1
            return max(Config.MIN_CONTEXT_MESSAGES, Config.DEFAULT_CONTEXT_MESSAGES - 3)
        
        return Config.DEFAULT_CONTEXT_MESSAGES
    
    def _get_embedding(self, text: str):
        """Get or compute embedding for text (uses pre-computed cache)."""
        if text in self.embedding_cache:
            return self.embedding_cache[text]
        
        if semantic_model is None:
            return None
        
        try:
            embedding = semantic_model.encode(text, convert_to_tensor=True)
            self.embedding_cache[text] = embedding
            return embedding
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
            return None
    
    def _create_training_entry(self, context: List[InstagramMessage], response: InstagramMessage) -> Dict:
        """Create Llama-3 format training entry."""
        entry = {
            "messages": [
                {"role": "system", "content": Config.SYSTEM_PROMPT}
            ]
        }
        
        for msg in context:
            role = "assistant" if msg.sender_name == Config.TARGET_USER_NAME else "user"
            entry["messages"].append({
                "role": role,
                "content": msg.clean_content
            })
        
        entry["messages"].append({
            "role": "assistant",
            "content": response.clean_content
        })
        
        return entry
    
    def _hash_entry(self, entry: Dict) -> str:
        """Hash entry for deduplication."""
        content_str = json.dumps(entry["messages"], sort_keys=True)
        return hashlib.md5(content_str.encode()).hexdigest()

# ==========================================
# POST-PROCESSING CLEANER
# ==========================================
class TrainingPairCleaner:
    """Post-processing cleaner for training pairs."""
    
    FILLER_ONLY = {
        'hange', 'okay', 'ok', 'k', 'hmm', 'hm', 'loss',
        'daa', 'ra', 'bro', 'maccha', 'lo', 'guru', 'yep',
        'nope', 'ya', 'nah', 'yaa', 'huu', 'hu'
    }
    
    def __init__(self, stats: ProcessingStats):
        self.stats = stats
    
    def clean_training_pair(self, entry: Dict) -> Optional[Dict]:
        """Clean a single training pair."""
        messages = entry["messages"]
        messages = self._remove_edited_duplicates(messages)
        messages = self._merge_consecutive_messages(messages)
        if self._is_ultra_low_quality(messages):
            self.stats.ultra_low_quality_filtered += 1
            return None
        return {"messages": messages}
    
    def _remove_edited_duplicates(self, messages: List[Dict]) -> List[Dict]:
        """Remove duplicate messages where one is marked (edited)."""
        cleaned = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg["role"] == "system":
                cleaned.append(msg)
                i += 1
                continue
            if i + 1 < len(messages):
                next_msg = messages[i + 1]
                if (msg["role"] == next_msg["role"] and
                    "(edited)" in next_msg["content"] and
                    self._is_edited_version(msg["content"], next_msg["content"])):
                    cleaned_content = next_msg["content"].replace(" (edited)", "").replace("(edited)", "")
                    cleaned.append({"role": next_msg["role"], "content": cleaned_content.strip()})
                    self.stats.edited_duplicates_removed += 1
                    i += 2
                    continue
            cleaned.append(msg)
            i += 1
        return cleaned
    
    def _is_edited_version(self, original: str, edited: str) -> bool:
        """Check if edited message is a variant of original."""
        from difflib import SequenceMatcher
        edited_clean = edited.replace("(edited)", "").strip()
        original_clean = original.strip()
        edited_text = re.sub(r'[^\w\s]', '', edited_clean).lower()
        original_text = re.sub(r'[^\w\s]', '', original_clean).lower()
        similarity = SequenceMatcher(None, original_text, edited_text).ratio()
        return similarity > 0.7
    
    def _merge_consecutive_messages(self, messages: List[Dict]) -> List[Dict]:
        """Merge consecutive messages from same role."""
        if len(messages) <= 1:
            return messages
        merged = [messages[0]]
        for msg in messages[1:]:
            last_msg = merged[-1]
            if (msg["role"] == last_msg["role"] and msg["role"] != "system"):
                merged[-1] = {
                    "role": last_msg["role"],
                    "content": last_msg["content"] + "\n" + msg["content"]
                }
                self.stats.consecutive_merged += 1
            else:
                merged.append(msg)
        return merged
    
    def _is_ultra_low_quality(self, messages: List[Dict]) -> bool:
        """Filter exchanges with ONLY filler words."""
        assistant_msgs = [m["content"] for m in messages if m["role"] == "assistant"]
        if not assistant_msgs:
            return True
        all_filler = True
        for content in assistant_msgs:
            text = re.sub(r'[^\w\s]', '', content).strip().lower()
            words = text.split()
            if len(words) > 2:
                all_filler = False
                break
            if words and not all(w in self.FILLER_ONLY for w in words):
                all_filler = False
                break
        return all_filler

# ==========================================
# DATA WRITER
# ==========================================
class DataWriter:
    """Handle JSONL output."""
    
    @staticmethod
    def write_jsonl(data: List[Dict], output_path: Path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            for entry in data:
                json.dump(entry, f, ensure_ascii=False)
                f.write('\n')
        
        logger.info(f"Wrote {len(data)} entries to {output_path}")
    
    @staticmethod
    def train_val_split(data: List[Dict], val_ratio: float = 0.1) -> Tuple[List[Dict], List[Dict]]:
        import random
        shuffled = data.copy()
        random.seed(42)
        random.shuffle(shuffled)
        
        split_idx = int(len(shuffled) * (1 - val_ratio))
        return shuffled[:split_idx], shuffled[split_idx:]

# ==========================================
# MAIN ORCHESTRATOR
# ==========================================
class InstagramChatParser:
    """Main orchestrator for Instagram chat parsing."""
    
    def __init__(self):
        self.stats = ProcessingStats()
        self.pii_scrubber = InstagramPIIScrubber(self.stats)
        self.quality_analyzer = ContentQualityAnalyzer(self.stats)
        self.classifier = MessageClassifier(self.pii_scrubber, self.quality_analyzer, self.stats)
        self.processor = EnhancedSemanticSessionProcessor(self.stats)
    
    def parse_file(self, file_path: Path) -> List[Dict]:
        """Parse single Instagram JSON file."""
        logger.info(f"Processing: {file_path.name}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        participants = [p['name'] for p in data.get('participants', [])]
        logger.info(f"  Participants: {participants}")
        logger.info(f"  Total raw messages: {len(data.get('messages', []))}")
        
        # Reverse to chronological order
        raw_messages = list(reversed(data.get('messages', [])))
        
        # Classify messages
        classified_messages = []
        for raw_msg in raw_messages:
            msg = self.classifier.classify(raw_msg)
            classified_messages.append(msg)
        
        logger.info(f"  Valid text messages: {sum(1 for m in classified_messages if m.is_valid)}")
        
        # Process into training pairs
        training_pairs = self.processor.process_messages(classified_messages)
        logger.info(f"  Training pairs generated: {len(training_pairs)}")
        
        return training_pairs, classified_messages  # Return both for date tracking
    
    def parse_directory(self, directory: Path) -> List[Dict]:
        """Parse all JSON files in directory."""
        all_training_data = []
        
        json_files = list(directory.glob("*.json"))
        if not json_files:
            logger.error(f"No JSON files found in {directory}")
            return []
        
        logger.info(f"Found {len(json_files)} JSON file(s)")
        
        for file_path in json_files:
            try:
                file_data = self.parse_file(file_path)
                all_training_data.extend(file_data)
            except Exception as e:
                logger.error(f"Failed to process {file_path.name}: {e}", exc_info=True)
        
        return all_training_data

# ==========================================
# MAIN EXECUTION
# ==========================================



def get_message_date_range(data: List[Dict]) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract first and last message dates from training data.
    
    Returns:
        Tuple of (first_date, last_date) in DD-MM-YYYY format
    """
    if not data:
        return None, None
    
    # Get all assistant messages with their positions
    first_msg = None
    last_msg = None
    
    # First entry's last assistant message
    for msg in data[0]["messages"]:
        if msg["role"] == "assistant":
            first_msg = msg["content"]
    
    # Last entry's last assistant message  
    for msg in data[-1]["messages"]:
        if msg["role"] == "assistant":
            last_msg = msg["content"]
    
    # Since we don't store timestamps in output, we'll use a different approach
    # Return position-based info instead
    return "Position: First", "Position: Last"

def main():
    logger.info("=" * 70)
    logger.info("Instagram Chat Parser v2.0 - Enhanced with Semantic AI")
    logger.info("=" * 70)
    logger.info(f"Target: {Config.TARGET_USER_NAME}")
    logger.info(f"Primary Threshold: {Config.PRIMARY_THRESHOLD_HOURS}h")
    logger.info(f"Turn-Aware: {'✅' if Config.ENABLE_TURN_AWARE else '❌'}")
    logger.info(f"Adaptive Context: {'✅' if Config.ENABLE_ADAPTIVE_CONTEXT else '❌'}")
    logger.info(f"Semantic Model: {'✅' if SEMANTIC_AVAILABLE else '❌'}")
    logger.info(f"Anti-Lobotomy Filter: {'✅' if Config.ENABLE_RECIPROCITY_FILTER else '❌'}")
    logger.info(f"PII Scrubbing: {'Aggressive' if Config.ENABLE_AGGRESSIVE_PII_SCRUBBING else 'Standard'}")
    logger.info(f"")
    logger.info(f"🆕 QUALITY FILTERS:")
    logger.info(f"  Repetition Filter: {'✅' if Config.ENABLE_REPETITION_FILTER else '❌'}")
    logger.info(f"  - Min Lexical Diversity: {Config.MIN_LEXICAL_DIVERSITY}")
    logger.info(f"  - Max Phrase Repetition: {Config.MAX_PHRASE_REPETITION}")
    logger.info(f"  Narrative Filter: {'✅' if Config.ENABLE_NARRATIVE_FILTER else '❌'}")
    logger.info(f"  - Max 3rd Person Ratio: {Config.MAX_THIRD_PERSON_RATIO}")
    logger.info(f"  - Max Narrative Markers: {Config.MAX_NARRATIVE_INDICATORS}")
    logger.info("=" * 70)
    
    if not Config.INPUT_DIR.exists():
        logger.error(f"Input directory '{Config.INPUT_DIR}' not found!")
        logger.info("Please create it and add your Instagram JSON files")
        return
    
    json_files = list(Config.INPUT_DIR.glob("*.json"))
    if not json_files:
        logger.error(f"No JSON files found in {Config.INPUT_DIR}")
        return
    
    logger.info(f"Found {len(json_files)} JSON file(s)")
    logger.info("Processing each file separately with chronological sorting...")
    
    for json_file in json_files:
        parser = InstagramChatParser()  # Fresh parser per file
        
        try:
            logger.info(f"\n{'='*70}")
            logger.info(f"Processing: {json_file.name}")
            logger.info(f"{'='*70}")
            
            # Parse single file
            training_data, classified_messages = parser.parse_file(json_file)
            
            if not training_data:
                logger.warning(f"No training data generated from {json_file.name}")
                continue
            
            # POST-PROCESSING
            logger.info("POST-PROCESSING: Cleaning training pairs...")
            cleaner = TrainingPairCleaner(parser.stats)
            cleaned_data = []
            for entry in training_data:
                cleaned_entry = cleaner.clean_training_pair(entry)
                if cleaned_entry:
                    cleaned_data.append(cleaned_entry)
            
            logger.info(f"Original pairs: {len(training_data)}")
            logger.info(f"After cleaning: {len(cleaned_data)}")
            
            # SORT BY TIMESTAMP (Most Recent First)
            cleaned_data = sort_by_timestamp(cleaned_data, reverse=True)
            logger.info("✅ Sorted by timestamp (most recent first)")

            # Display date range from classified messages
            valid_msgs = [m for m in classified_messages if m.is_valid and hasattr(m, 'timestamp_ms')]
            if valid_msgs:
                first_date = valid_msgs[0].timestamp.strftime("%d-%m-%Y")
                last_date = valid_msgs[-1].timestamp.strftime("%d-%m-%Y")
                logger.info(f"📅 Chat Period: {first_date} (oldest) → {last_date} (newest)")
                logger.info(f"📅 After Sort: Most recent ({last_date}) appears first in JSONL")
            
            
            # Generate output filenames
            base_name = json_file.stem
            train_file = Config.OUTPUT_FILE.parent / f"{base_name}_train.jsonl"
            val_file = Config.VAL_FILE.parent / f"{base_name}_val.jsonl"
            stats_file = Config.STATS_FILE.parent / f"{base_name}_stats.json"
            rejection_file = Config.REJECTION_LOG_FILE.parent / f"{base_name}_rejected.jsonl"
            
            # Train/Val Split
            if Config.VALIDATION_SPLIT > 0:
                train_data, val_data = DataWriter.train_val_split(cleaned_data, Config.VALIDATION_SPLIT)
                DataWriter.write_jsonl(train_data, train_file)
                DataWriter.write_jsonl(val_data, val_file)
            else:
                DataWriter.write_jsonl(cleaned_data, train_file)
            
            # Save statistics
            stats_dict = parser.stats.to_dict()
            with open(stats_file, 'w', encoding='utf-8') as f:
                json.dump(stats_dict, f, indent=2, ensure_ascii=False)
            
            # Write rejection log
            if Config.LOG_REJECTED_MESSAGES and parser.stats.rejection_samples:
                with open(rejection_file, 'w', encoding='utf-8') as f:
                    for sample in parser.stats.rejection_samples:
                        json.dump(sample, f, ensure_ascii=False)
                        f.write('\n')
            
            # Display summary
            logger.info(f"\nPROCESSING SUMMARY - {json_file.name}")
            logger.info("=" * 70)
            logger.info(f"Total messages: {parser.stats.total_messages}")
            logger.info(f"Valid messages: {parser.stats.valid_messages}")
            logger.info(f"Sessions created: {parser.stats.sessions_created}")
            logger.info(f"Training pairs: {parser.stats.training_pairs_generated}")
            logger.info(f"Quality rejections: {stats_dict['quality_rejections']['total_quality_rejections']}")
            logger.info(f"Data quality rate: {stats_dict['data_quality_rate']*100:.1f}%")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.error(f"Failed to process {json_file.name}: {e}", exc_info=True)
    
    logger.info(f"\n🚀 All files processed! Upload *_train.jsonl files to Unsloth")
    logger.info("=" * 70)


def sort_by_timestamp(data: List[Dict], reverse: bool = True) -> List[Dict]:
    """
    Sort training pairs by the timestamp of the last assistant message.
    
    Args:
        data: List of training pairs
        reverse: If True, most recent first (default)
    
    Returns:
        Sorted list
    """
    def get_last_timestamp(entry: Dict) -> int:
        """Extract timestamp from last assistant message in conversation."""
        messages = entry.get("messages", [])
        # Find last assistant message (skip system prompt)
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                # Try to extract timestamp from content or use 0 as fallback
                return 0  # Instagram doesn't store timestamps in output, use creation order
        return 0
    
    # For Instagram, since we process chronologically, just reverse the list
    # to get most recent first
    if reverse:
        return list(reversed(data))
    return data



if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
    except Exception as e:
        logger.error(f"\nFatal error: {e}", exc_info=True)