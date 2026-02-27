"""
WhatsApp Chat to Unsloth/Llama-3 Converter (Industry-Grade Edition v10)
========================================================================
Production-grade parser with advanced content quality analysis.

NEW in v10:
-----------
✨ Repetition detection: Filters spam like "Cool cool cool" x50
✨ Narrative detection: Rejects 3rd-person stories that confuse training
✨ Lexical diversity scoring: Ensures meaningful vocabulary variation
✨ Enhanced quality metrics and detailed rejection logging

Key Enhancements:
- Lexical Diversity: Measures unique_words/total_words ratio
- Phrase Loop Detection: Catches "cool cool cool" patterns
- Third-Person Narrative Filter: Rejects stories with he/she/they overuse
- Story Marker Detection: Identifies "once upon a time" patterns
- Comprehensive Quality Scoring: Weighted 0.0-1.0 scale
- Detailed Rejection Analytics: Track why messages fail
"""

import re
import json
import logging
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass, field
from collections import Counter, deque
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
    """Research-backed configuration for optimal training data generation."""
    
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
    # SESSION GROUPING
    PRIMARY_THRESHOLD_HOURS: float = float(os.getenv("PRIMARY_THRESHOLD_HOURS", "2.0"))
    SOFT_THRESHOLD_HOURS: float = float(os.getenv("SOFT_THRESHOLD_HOURS", "4.0"))
    ENABLE_TURN_AWARE: bool = os.getenv("ENABLE_TURN_AWARE", "true").lower() == "true"
    MIN_BURST_MESSAGES: int = int(os.getenv("MIN_BURST_MESSAGES", "3"))
    
    # Semantic parameters  
    SEMANTIC_THRESHOLD: float = float(os.getenv("SEMANTIC_THRESHOLD", "0.4"))
    MIN_MSG_LEN_FOR_SEMANTIC: int = int(os.getenv("MIN_MSG_LEN_FOR_SEMANTIC", "25"))
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
    # 🆕 ADVANCED CONTENT QUALITY FILTERS
    # ============================================================
    
    # Repetition Detection (for "Cool cool cool" spam)
    ENABLE_REPETITION_FILTER: bool = os.getenv("ENABLE_REPETITION_FILTER", "true").lower() == "true"
    MIN_LEXICAL_DIVERSITY: float = float(os.getenv("MIN_LEXICAL_DIVERSITY", "0.4"))  # unique/total ratio
    MAX_PHRASE_REPETITION: int = int(os.getenv("MAX_PHRASE_REPETITION", "5"))  # consecutive repeats
    MIN_WORDS_FOR_DIVERSITY_CHECK: int = int(os.getenv("MIN_WORDS_FOR_DIVERSITY_CHECK", "10"))
    
    # Narrative/Story Detection (for Koushik & Himaja stories)
    ENABLE_NARRATIVE_FILTER: bool = os.getenv("ENABLE_NARRATIVE_FILTER", "true").lower() == "true"
    MAX_THIRD_PERSON_RATIO: float = float(os.getenv("MAX_THIRD_PERSON_RATIO", "0.3"))  # he/she/they ratio
    MAX_NARRATIVE_INDICATORS: int = int(os.getenv("MAX_NARRATIVE_INDICATORS", "3"))  # story markers
    MAX_PARAGRAPH_BREAKS: int = int(os.getenv("MAX_PARAGRAPH_BREAKS", "8"))  # excessive newlines
    MIN_WORDS_FOR_NARRATIVE_CHECK: int = int(os.getenv("MIN_WORDS_FOR_NARRATIVE_CHECK", "50"))
    
    # Combined Quality Score
    MIN_QUALITY_SCORE: float = float(os.getenv("MIN_QUALITY_SCORE", "0.5"))  # 0.0-1.0 scale
    
    # Logging
    LOG_REJECTED_MESSAGES: bool = os.getenv("LOG_REJECTED_MESSAGES", "true").lower() == "true"
    MAX_REJECTION_SAMPLES: int = int(os.getenv("MAX_REJECTION_SAMPLES", "20"))
    
    # Paths
    INPUT_DIR: Path = Path(os.getenv("INPUT_DIR", "raw_chats"))
    OUTPUT_FILE: Path = Path(os.getenv("OUTPUT_FILE", "train.jsonl"))
    VAL_FILE: Path = Path(os.getenv("VAL_FILE", "val.jsonl"))
    STATS_FILE: Path = Path(os.getenv("STATS_FILE", "processing_stats.json"))
    REJECTION_LOG_FILE: Path = Path(os.getenv("REJECTION_LOG_FILE", "rejected_messages.jsonl"))
    
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

# ==========================================
# SETUP
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('parser.log', encoding='utf-8')
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
# CONTENT QUALITY ANALYZER (NEW MODULE)
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

class ContentQualityAnalyzer:
    """
    Advanced content quality analysis for filtering spam and narratives.
    
    Responsibilities:
    1. Detect repetitive spam (e.g., "Cool cool cool" x50)
    2. Identify 3rd-person narratives that confuse training
    3. Calculate lexical diversity and quality scores
    """
    
    # Narrative story markers
    NARRATIVE_MARKERS = [
        r'\bonce upon a time\b',
        r'\bin a (land|kingdom|village|city|world|place)\b',
        r'\bthere (was|were|lived)\b',
        r'\bthe story (of|begins|continues)\b',
        r'\bchapter \d+\b',
        r'\bto be continued\b',
        r'\bthe end\b',
        r'\blong ago\b',
        r'\bmany years (ago|later)\b'
    ]
    
    # Third-person pronouns (excluding "you")
    THIRD_PERSON_PRONOUNS = r'\b(he|she|they|him|her|them|his|hers|their|theirs)\b'
    
    @staticmethod
    def analyze(text: str) -> QualityMetrics:
        """
        Comprehensive quality analysis pipeline.
        
        Args:
            text: Cleaned message content
            
        Returns:
            QualityMetrics with all scores and pass/fail status
        """
        metrics = QualityMetrics()
        
        # Extract words for analysis
        words = re.findall(r'\b\w+\b', text.lower())
        
        if not words:
            metrics.passed = False
            metrics.rejection_reason = RejectionReason.INSUFFICIENT_WORDS
            return metrics
        
        # === STEP 1: REPETITION ANALYSIS ===
        if Config.ENABLE_REPETITION_FILTER and len(words) >= Config.MIN_WORDS_FOR_DIVERSITY_CHECK:
            metrics.lexical_diversity = ContentQualityAnalyzer._calculate_lexical_diversity(words)
            metrics.max_phrase_repetition = ContentQualityAnalyzer._detect_phrase_loops(words)
            
            # Check: Lexical diversity too low?
            if metrics.lexical_diversity < Config.MIN_LEXICAL_DIVERSITY:
                metrics.passed = False
                metrics.rejection_reason = RejectionReason.LOW_LEXICAL_DIVERSITY
                return metrics
            
            # Check: Excessive phrase repetition?
            if metrics.max_phrase_repetition > Config.MAX_PHRASE_REPETITION:
                metrics.passed = False
                metrics.rejection_reason = RejectionReason.EXCESSIVE_REPETITION
                return metrics
        
        # === STEP 2: NARRATIVE DETECTION ===
        if Config.ENABLE_NARRATIVE_FILTER and len(words) >= Config.MIN_WORDS_FOR_NARRATIVE_CHECK:
            metrics.third_person_ratio = ContentQualityAnalyzer._calculate_third_person_ratio(text, words)
            metrics.narrative_indicators = ContentQualityAnalyzer._count_narrative_markers(text)
            metrics.paragraph_breaks = text.count('\n\n') + text.count('\n\n\n')
            
            # Check: Too many third-person pronouns?
            if metrics.third_person_ratio > Config.MAX_THIRD_PERSON_RATIO:
                metrics.passed = False
                metrics.rejection_reason = RejectionReason.HIGH_THIRD_PERSON
                return metrics
            
            # Check: Multiple narrative markers?
            if metrics.narrative_indicators > Config.MAX_NARRATIVE_INDICATORS:
                metrics.passed = False
                metrics.rejection_reason = RejectionReason.NARRATIVE_DETECTED
                return metrics
        
        # === STEP 3: OVERALL QUALITY SCORE ===
        metrics.overall_score = ContentQualityAnalyzer._calculate_quality_score(metrics)
        
        if metrics.overall_score < Config.MIN_QUALITY_SCORE:
            metrics.passed = False
            metrics.rejection_reason = RejectionReason.LOW_QUALITY_SCORE
            return metrics
        
        return metrics
    
    @staticmethod
    def _calculate_lexical_diversity(words: List[str]) -> float:
        """
        Lexical diversity = unique_words / total_words
        
        Examples:
        - "cool cool cool cool" -> 1/4 = 0.25 (SPAM)
        - "that is cool cool" -> 3/4 = 0.75 (GOOD)
        - "I think that's really cool" -> 5/5 = 1.0 (EXCELLENT)
        """
        if not words:
            return 0.0
        unique_count = len(set(words))
        total_count = len(words)
        return unique_count / total_count
    
    @staticmethod
    def _detect_phrase_loops(words: List[str]) -> int:
        """
        Detect consecutive repeated words/phrases.
        
        Returns: Maximum repetition count of any word/phrase
        
        Example:
        - "cool cool cool cool" -> 4 (FAIL if MAX_PHRASE_REPETITION=5)
        - "ok ok, I see" -> 2 (PASS)
        """
        if len(words) < 2:
            return 0
        
        max_repetition = 0
        
        # Check 1-word repetition
        current_word = words[0]
        current_count = 1
        
        for word in words[1:]:
            if word == current_word:
                current_count += 1
                max_repetition = max(max_repetition, current_count)
            else:
                current_word = word
                current_count = 1
        
        # Check 2-word phrase repetition
        if len(words) >= 4:
            for i in range(len(words) - 3):
                phrase = (words[i], words[i+1])
                phrase_count = 1
                j = i + 2
                while j < len(words) - 1:
                    if (words[j], words[j+1]) == phrase:
                        phrase_count += 1
                        j += 2
                    else:
                        break
                max_repetition = max(max_repetition, phrase_count)
        
        return max_repetition
    
    @staticmethod
    def _calculate_third_person_ratio(text: str, words: List[str]) -> float:
        """
        Calculate ratio of 3rd-person pronouns to total words.
        
        High ratio indicates narrative/story content.
        
        Example:
        - "He went to the store. She bought milk." -> High ratio (NARRATIVE)
        - "I went to the store. You should go too." -> Low ratio (CHAT)
        """
        if not words:
            return 0.0
        
        third_person_matches = re.findall(ContentQualityAnalyzer.THIRD_PERSON_PRONOUNS, text.lower())
        third_person_count = len(third_person_matches)
        return third_person_count / len(words)
    
    @staticmethod
    def _count_narrative_markers(text: str) -> int:
        """
        Count story/narrative indicators.
        
        Examples of markers:
        - "Once upon a time"
        - "There was a boy"
        - "Chapter 1"
        - "The end"
        """
        count = 0
        text_lower = text.lower()
        for pattern in ContentQualityAnalyzer.NARRATIVE_MARKERS:
            matches = re.findall(pattern, text_lower)
            count += len(matches)
        return count
    
    @staticmethod
    def _calculate_quality_score(metrics: QualityMetrics) -> float:
        """
        Aggregate quality score (0.0 - 1.0).
        
        Weighted formula:
        - Lexical diversity: 50% weight
        - Repetition penalty: 30% weight
        - Narrative penalty: 20% weight
        """
        score = 0.0
        
        # Component 1: Lexical diversity (50%)
        if metrics.lexical_diversity > 0:
            diversity_normalized = min(1.0, metrics.lexical_diversity / Config.MIN_LEXICAL_DIVERSITY)
            score += 0.5 * diversity_normalized
        
        # Component 2: Repetition penalty (30%)
        if Config.ENABLE_REPETITION_FILTER:
            if Config.MAX_PHRASE_REPETITION > 0:
                rep_penalty = metrics.max_phrase_repetition / Config.MAX_PHRASE_REPETITION
                rep_score = max(0.0, 1.0 - rep_penalty)
            else:
                rep_score = 1.0
            score += 0.3 * rep_score
        else:
            score += 0.3  # Full credit if filter disabled
        
        # Component 3: Narrative penalty (20%)
        if Config.ENABLE_NARRATIVE_FILTER:
            narrative_score = 1.0
            
            # Penalty from third-person ratio
            if Config.MAX_THIRD_PERSON_RATIO > 0 and metrics.third_person_ratio > 0:
                third_person_penalty = metrics.third_person_ratio / Config.MAX_THIRD_PERSON_RATIO
                narrative_score *= max(0.0, 1.0 - third_person_penalty)
            
            # Penalty from narrative markers
            if Config.MAX_NARRATIVE_INDICATORS > 0 and metrics.narrative_indicators > 0:
                marker_penalty = metrics.narrative_indicators / Config.MAX_NARRATIVE_INDICATORS
                narrative_score *= max(0.0, 1.0 - marker_penalty)
            
            score += 0.2 * narrative_score
        else:
            score += 0.2  # Full credit if filter disabled
        
        return min(1.0, score)

# ==========================================
# DATA STRUCTURES
# ==========================================
@dataclass
class Message:
    """Message with enhanced validation and quality metrics."""
    timestamp: datetime
    sender: str
    content: str
    clean_content: str
    is_system: bool = False
    is_valid: bool = True
    quality_metrics: Optional[QualityMetrics] = None
    rejection_reason: Optional[RejectionReason] = None
    
    def __post_init__(self):
        """Validate message on creation."""
        if not self.is_system:
            self._validate()
    
    def _validate(self) -> None:
        """
        Enhanced validation pipeline:
        1. Basic length checks
        2. Word count validation
        3. Advanced quality analysis (NEW)
        """
        # Check 1: Minimum length
        if not self.clean_content or len(self.clean_content.strip()) < Config.MIN_MESSAGE_LENGTH:
            self.is_valid = False
            self.rejection_reason = RejectionReason.TOO_SHORT
            return
        
        # Check 2: Maximum length
        if len(self.clean_content) > Config.MAX_MESSAGE_LENGTH:
            self.is_valid = False
            self.rejection_reason = RejectionReason.TOO_LONG
            return
        
        # Check 3: Minimum words
        words = re.findall(r'\b\w+\b', self.clean_content)
        if len(words) < Config.MIN_MEANINGFUL_WORDS:
            self.is_valid = False
            self.rejection_reason = RejectionReason.INSUFFICIENT_WORDS
            return
        
        # Check 4: Advanced quality analysis (NEW in v10)
        self.quality_metrics = ContentQualityAnalyzer.analyze(self.clean_content)
        
        if not self.quality_metrics.passed:
            self.is_valid = False
            self.rejection_reason = self.quality_metrics.rejection_reason
            return

@dataclass
class ProcessingStats:
    """Comprehensive processing statistics with quality tracking."""
    total_messages: int = 0
    parsed_messages: int = 0
    system_messages: int = 0
    invalid_messages: int = 0
    deleted_messages: int = 0
    payment_messages: int = 0
    media_only_messages: int = 0
    
    # 🆕 Quality-related rejections
    rejected_low_diversity: int = 0
    rejected_repetition: int = 0
    rejected_narrative: int = 0
    rejected_third_person: int = 0
    rejected_low_quality: int = 0
    
    training_pairs_generated: int = 0
    duplicates_removed: int = 0
    semantic_splits: int = 0
    time_gap_splits: int = 0
    turn_preservations: int = 0
    adaptive_context_adjustments: int = 0
    low_quality_sessions_filtered: int = 0
    failed_timestamp_parse: int = 0
    messages_by_sender: Counter = field(default_factory=Counter)
    sessions_created: int = 0
    rejection_samples: List[Dict] = field(default_factory=list)
    
    # Post-processing stats
    consecutive_merged: int = 0
    edited_duplicates_removed: int = 0
    ultra_low_quality_filtered: int = 0
    
    def record_rejection(self, message: Message):
        """Track rejection reasons and samples."""
        if not message.rejection_reason:
            return
        
        # Categorize rejection
        if message.rejection_reason == RejectionReason.LOW_LEXICAL_DIVERSITY:
            self.rejected_low_diversity += 1
        elif message.rejection_reason == RejectionReason.EXCESSIVE_REPETITION:
            self.rejected_repetition += 1
        elif message.rejection_reason == RejectionReason.NARRATIVE_DETECTED:
            self.rejected_narrative += 1
        elif message.rejection_reason == RejectionReason.HIGH_THIRD_PERSON:
            self.rejected_third_person += 1
        elif message.rejection_reason == RejectionReason.LOW_QUALITY_SCORE:
            self.rejected_low_quality += 1
        
        # Sample logging for debugging
        if Config.LOG_REJECTED_MESSAGES and len(self.rejection_samples) < Config.MAX_REJECTION_SAMPLES:
            sample = {
                'timestamp': message.timestamp.isoformat(),
                'sender': message.sender,
                'content_preview': message.clean_content[:200] + ('...' if len(message.clean_content) > 200 else ''),
                'reason': message.rejection_reason.value,
                'quality_metrics': {}
            }
            
            if message.quality_metrics:
                sample['quality_metrics'] = {
                    'lexical_diversity': round(message.quality_metrics.lexical_diversity, 3),
                    'max_repetition': message.quality_metrics.max_phrase_repetition,
                    'third_person_ratio': round(message.quality_metrics.third_person_ratio, 3),
                    'narrative_indicators': message.quality_metrics.narrative_indicators,
                    'quality_score': round(message.quality_metrics.overall_score, 3)
                }
            
            self.rejection_samples.append(sample)
    
    def to_dict(self) -> Dict:
        """Export stats to dictionary."""
        total_quality_rejections = (
            self.rejected_low_diversity + self.rejected_repetition + 
            self.rejected_narrative + self.rejected_third_person + self.rejected_low_quality
        )
        
        return {
            'total_messages': self.total_messages,
            'parsed_messages': self.parsed_messages,
            'system_messages': self.system_messages,
            'invalid_messages': self.invalid_messages,
            'deleted_messages': self.deleted_messages,
            'payment_messages': self.payment_messages,
            'media_only_messages': self.media_only_messages,
            'quality_rejections': {
                'low_lexical_diversity': self.rejected_low_diversity,
                'excessive_repetition': self.rejected_repetition,
                'narrative_detected': self.rejected_narrative,
                'high_third_person': self.rejected_third_person,
                'low_overall_quality': self.rejected_low_quality,
                'total_quality_rejections': total_quality_rejections
            },
            'post_processing': {
                'consecutive_merged': self.consecutive_merged,
                'edited_duplicates_removed': self.edited_duplicates_removed,
                'ultra_low_quality_filtered': self.ultra_low_quality_filtered
            },
            'training_pairs_generated': self.training_pairs_generated,
            'duplicates_removed': self.duplicates_removed,
            'semantic_splits': self.semantic_splits,
            'time_gap_splits': self.time_gap_splits,
            'turn_preservations': self.turn_preservations,
            'adaptive_context_adjustments': self.adaptive_context_adjustments,
            'low_quality_sessions_filtered': self.low_quality_sessions_filtered,
            'sessions_created': self.sessions_created,
            'failed_timestamp_parse': self.failed_timestamp_parse,
            'messages_by_sender': dict(self.messages_by_sender),
            'data_quality_rate': round(self.parsed_messages / max(1, self.total_messages), 3),
            'training_efficiency': round(self.training_pairs_generated / max(1, self.parsed_messages), 3),
            'avg_training_pairs_per_session': round(self.training_pairs_generated / max(1, self.sessions_created), 2),
            'rejection_samples': self.rejection_samples[:Config.MAX_REJECTION_SAMPLES]
        }

# ==========================================
# PARSER ENGINE
# ==========================================
class WhatsAppParser:
    """Robust parser with comprehensive edge case handling."""
    
    SYSTEM_PATTERNS = [
        r'Messages and calls are end-to-end encrypted',
        r'You deleted this message',
        r'This message was deleted',
        r'You sent ₹[\d,]+\.?\d* to',
        r'You received ₹[\d,]+\.?\d* from',
        r'[^:]+\s+changed the subject to',
        r'[^:]+\s+changed this group\'s icon',
        r'[^:]+\s+added\s+[^:]+',
        r'[^:]+\s+removed\s+[^:]+',
        r'[^:]+\s+left',
        r'[^:]+\s+joined using this group\'s invite link',
        r'Security code changed',
        r'Missed voice call',
        r'Missed video call',
    ]
    
    SYSTEM_REGEX = re.compile('|'.join(SYSTEM_PATTERNS), re.IGNORECASE)
    MSG_PATTERN = re.compile(
        r'^(\d{1,2}[/-]\d{1,2}[/-]\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?\s?(?:[aApP][mM])?)\s*[-–—]\s*([^:]+):\s+(.+)',
        re.MULTILINE
    )
    
    def __init__(self, stats: ProcessingStats):
        self.stats = stats
    
    def parse_file(self, file_path: Path) -> List[Message]:
        """Parse file with robust encoding and error handling."""
        messages = []
        text = self._read_file_safely(file_path)
        if not text:
            return messages
        
        text = self._normalize_unicode(text)
        last_msg = None
        
        for line_num, line in enumerate(text.splitlines(), 1):
            self.stats.total_messages += 1
            match = self.MSG_PATTERN.match(line)
            
            if match:
                # Process previous message
                if last_msg and not last_msg.is_system:
                    if last_msg.is_valid:
                        messages.append(last_msg)
                        self.stats.parsed_messages += 1
                        self.stats.messages_by_sender[last_msg.sender] += 1
                    else:
                        self.stats.invalid_messages += 1
                        self.stats.record_rejection(last_msg)
                
                ts_str, sender, content = match.groups()
                timestamp = self._parse_timestamp(ts_str)
                
                if not timestamp:
                    self.stats.failed_timestamp_parse += 1
                    last_msg = None
                    continue
                
                if self._is_system_message(content):
                    self.stats.system_messages += 1
                    last_msg = None
                    continue
                
                if 'You deleted this message' in content:
                    self.stats.deleted_messages += 1
                    last_msg = None
                    continue
                
                if 'You sent ₹' in content or 'You received ₹' in content:
                    self.stats.payment_messages += 1
                    last_msg = None
                    continue
                
                clean_content = self._clean(content)
                last_msg = Message(
                    timestamp=timestamp,
                    sender=sender.strip(),
                    content=content.strip(),
                    clean_content=clean_content,
                    is_system=False
                )
                
                if clean_content == '[MEDIA_SENT]':
                    self.stats.media_only_messages += 1
                    last_msg.is_valid = False
                    last_msg.rejection_reason = RejectionReason.MEDIA_ONLY
            else:
                # Multi-line message continuation
                if last_msg and not last_msg.is_system:
                    if not self._is_system_message(line):
                        last_msg.content += f"\n{line}"
                        cleaned_line = self._clean(line)
                        if cleaned_line:
                            last_msg.clean_content += f"\n{cleaned_line}"
        
        # Process final message
        if last_msg and not last_msg.is_system:
            if last_msg.is_valid:
                messages.append(last_msg)
                self.stats.parsed_messages += 1
                self.stats.messages_by_sender[last_msg.sender] += 1
            else:
                self.stats.invalid_messages += 1
                self.stats.record_rejection(last_msg)
        
        return messages
    
    def _read_file_safely(self, file_path: Path) -> Optional[str]:
        """Multi-encoding support."""
        for encoding in ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    return f.read()
            except (UnicodeDecodeError, UnicodeError):
                continue
        logger.error(f"Could not read file {file_path} with any encoding")
        return None
    
    def _normalize_unicode(self, text: str) -> str:
        """Fix WhatsApp's special characters."""
        text = text.replace('â€¯', ' ')
        text = text.replace('\u202f', ' ')
        text = text.replace('\u00a0', ' ')
        text = text.replace('\u2009', ' ')
        text = text.replace('\u200b', '')
        text = text.replace('\ufeff', '')
        text = text.replace('–', '-')
        text = text.replace('—', '-')
        return text
    
    def _is_system_message(self, content: str) -> bool:
        return bool(self.SYSTEM_REGEX.search(content))
    
    def _parse_timestamp(self, ts_str: str) -> Optional[datetime]:
        """Try multiple timestamp formats."""
        formats = [
            '%d/%m/%y, %I:%M %p', '%d/%m/%Y, %I:%M %p',
            '%m/%d/%y, %I:%M %p', '%d/%m/%y, %H:%M',
            '%d/%m/%Y, %H:%M', '%d/%m/%y %I:%M %p',
            '%d/%m/%Y %I:%M %p', '%d/%m/%y %H:%M',
            '%d-%m-%y, %I:%M %p', '%d-%m-%Y, %I:%M %p',
            '%d/%m/%y, %I:%M:%S %p', '%d/%m/%Y, %I:%M:%S %p',
            '%d/%m/%y, %H:%M:%S', '%d/%m/%Y, %H:%M:%S',
        ]
        
        ts_str = ts_str.strip()
        ts_str = re.sub(r'\s+', ' ', ts_str)
        
        for ts_var in [ts_str, ts_str.replace(',', '')]:
            for fmt in formats:
                try:
                    return datetime.strptime(ts_var, fmt)
                except ValueError:
                    continue
        return None
    
    def _clean(self, text: str) -> str:
        """Comprehensive PII scrubbing."""
        if not text:
            return ""
        
        # Media replacements
        text = text.replace('<Media omitted>', '[MEDIA_SENT]')
        text = text.replace('image omitted', '[MEDIA_SENT]')
        text = text.replace('video omitted', '[MEDIA_SENT]')
        text = text.replace('audio omitted', '[MEDIA_SENT]')
        text = text.replace('sticker omitted', '[MEDIA_SENT]')
        text = text.replace('GIF omitted', '[MEDIA_SENT]')
        text = text.replace('document omitted', '[MEDIA_SENT]')
        
        # PII scrubbing
        text = re.sub(r'https?://\S+', '[LINK]', text)
        text = re.sub(r'www\.\S+', '[LINK]', text)
        text = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', '[EMAIL]', text)
        text = re.sub(r'\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}', '[PHONE]', text)
        text = re.sub(r'₹\s*[\d,]+\.?\d*', '[AMOUNT]', text)
        text = re.sub(r'\$\s*[\d,]+\.?\d+', '[AMOUNT]', text)
        
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

# ==========================================
# SEMANTIC PROCESSOR
# ==========================================
class EnhancedSemanticProcessor:
    """Advanced session grouping with turn-awareness and adaptive context."""
    
    def __init__(self, stats: ProcessingStats):
        self.stats = stats
        self.seen_hashes: Set[str] = set()
        self.topic_history: deque = deque(maxlen=Config.TOPIC_PERSISTENCE_THRESHOLD)
    
    def _is_message_burst(self, messages: List[Message], current_idx: int) -> bool:
        """Detect if current message is part of a burst."""
        if not Config.ENABLE_TURN_AWARE or current_idx < 1:
            return False
        
        current_sender = messages[current_idx].sender
        burst_count = 1
        
        for i in range(current_idx - 1, max(-1, current_idx - 10), -1):
            if messages[i].sender == current_sender:
                burst_count += 1
            else:
                break
        
        return burst_count >= Config.MIN_BURST_MESSAGES
    
    def _calculate_adaptive_context_size(self, recent_gaps: List[float]) -> int:
        """Adaptive context based on conversation density."""
        if not Config.ENABLE_ADAPTIVE_CONTEXT or not recent_gaps:
            return Config.DEFAULT_CONTEXT_MESSAGES
        
        avg_gap = sum(recent_gaps) / len(recent_gaps)
        
        if avg_gap < 5/60:  # <5 min: dense conversation
            self.stats.adaptive_context_adjustments += 1
            return min(Config.MAX_CONTEXT_MESSAGES, 15)
        elif avg_gap > 0.5:  # >30 min: sparse conversation
            self.stats.adaptive_context_adjustments += 1
            return max(Config.MIN_CONTEXT_MESSAGES, 5)
        else:
            return Config.DEFAULT_CONTEXT_MESSAGES
    
    def _check_topic_drift(self, curr_msg: str, context_buffer: List[Message]) -> bool:
        """Enhanced semantic drift with topic persistence."""
        if not SEMANTIC_AVAILABLE or not semantic_model:
            return False
        
        if len(curr_msg) < Config.MIN_MSG_LEN_FOR_SEMANTIC:
            return False
        
        try:
            curr_emb = semantic_model.encode(curr_msg, convert_to_tensor=True)
            recent_msgs = context_buffer[-Config.TOPIC_PERSISTENCE_THRESHOLD:]
            similarities = []
            
            for hist_msg in recent_msgs:
                if len(hist_msg.clean_content) >= Config.MIN_MSG_LEN_FOR_SEMANTIC:
                    hist_emb = semantic_model.encode(hist_msg.clean_content, convert_to_tensor=True)
                    sim = util.pytorch_cos_sim(curr_emb, hist_emb).item()
                    similarities.append(sim)
            
            if not similarities:
                return False
            
            avg_similarity = sum(similarities) / len(similarities)
            
            if avg_similarity < Config.SEMANTIC_THRESHOLD:
                self.stats.semantic_splits += 1
                return True
            
            return False
            
        except Exception as e:
            logger.warning(f"Semantic check failed: {e}")
            return False
    
    def _is_high_quality_session(self, context_buffer: List[Message]) -> bool:
        """Filter low-quality sessions."""
        if len(context_buffer) < Config.MIN_SESSION_EXCHANGES:
            return False
        
        senders = [m.sender for m in context_buffer]
        unique_senders = len(set(senders))
        if unique_senders < 2:
            return False
        
        duration = (context_buffer[-1].timestamp - context_buffer[0].timestamp).total_seconds() / 3600
        if duration > Config.MAX_SESSION_DURATION_HOURS:
            return False
        
        return True
    
    def generate_sliding_windows(self, messages: List[Message]) -> List[Dict]:
        """Industry-grade session grouping with multiple signals."""
        dataset = []
        context_buffer: List[Message] = []
        last_ts = None
        recent_gaps = deque(maxlen=5)
        
        for i, msg in enumerate(messages):
            if not msg.is_valid or msg.is_system:
                continue
            
            should_reset = False
            reset_reason = None
            
            if last_ts and context_buffer:
                gap_hours = (msg.timestamp - last_ts).total_seconds() / 3600
                recent_gaps.append(gap_hours)
                
                if gap_hours > Config.PRIMARY_THRESHOLD_HOURS:
                    if Config.ENABLE_TURN_AWARE and self._is_message_burst(messages, i):
                        if gap_hours > Config.SOFT_THRESHOLD_HOURS:
                            should_reset = True
                            reset_reason = "time_gap_burst"
                        else:
                            self.stats.turn_preservations += 1
                    else:
                        should_reset = True
                        reset_reason = "time_gap"
                
                if not should_reset and msg.sender == Config.TARGET_USER_NAME:
                    if self._check_topic_drift(msg.clean_content, context_buffer):
                        should_reset = True
                        reset_reason = "semantic_drift"
            
            if should_reset:
                if reset_reason in ["time_gap", "time_gap_burst"]:
                    self.stats.time_gap_splits += 1
                self.stats.sessions_created += 1
                context_buffer = []
            
            last_ts = msg.timestamp
            
            if msg.sender == Config.TARGET_USER_NAME and len(context_buffer) >= Config.MIN_CONTEXT_MESSAGES:
                if not self._is_high_quality_session(context_buffer + [msg]):
                    self.stats.low_quality_sessions_filtered += 1
                else:
                    context_size = self._calculate_adaptive_context_size(list(recent_gaps))
                    relevant_context = context_buffer[-context_size:] if len(context_buffer) > context_size else context_buffer
                    
                    entry = {
                        "messages": [
                            {"role": "system", "content": Config.SYSTEM_PROMPT}
                        ]
                    }
                    
                    for hist_msg in relevant_context:
                        role = "assistant" if hist_msg.sender == Config.TARGET_USER_NAME else "user"
                        entry["messages"].append({
                            "role": role,
                            "content": hist_msg.clean_content
                        })
                    
                    entry["messages"].append({
                        "role": "assistant",
                        "content": msg.clean_content
                    })
                    
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
            
            context_buffer.append(msg)
        
        return dataset
    
    def _hash_entry(self, entry: Dict) -> str:
        content_str = json.dumps(entry["messages"], sort_keys=True)
        return hashlib.md5(content_str.encode()).hexdigest()


# ==========================================
# POST-PROCESSING CLEANER (NEW MODULE)
# ==========================================
class TrainingPairCleaner:
    """
    Post-processing cleaner for training pairs.
    
    Fixes three critical noise patterns:
    1. Consecutive assistant messages (multi-burst)
    2. Edited message duplicates
    3. Ultra-low-quality filler exchanges
    """
    
    # Ultra-low-quality fillers (only if ALL messages are these)
    FILLER_ONLY = {
        'hange', 'okay', 'ok', 'k', 'hmm', 'hm', 'loss', 
        'daa', 'ra', 'bro', 'maccha', 'lo', 'guru', 'yep',
        'nope', 'ya', 'nah', 'yaa', 'huu', 'hu'
    }
    
    def __init__(self, stats: 'ProcessingStats'):
        self.stats = stats
    
    def clean_training_pair(self, entry: Dict) -> Optional[Dict]:
        """
        Clean a single training pair.
        Returns None if should be filtered entirely.
        """
        messages = entry["messages"]
        
        # Step 1: Remove edited duplicates
        messages = self._remove_edited_duplicates(messages)
        
        # Step 2: Merge consecutive messages from same role
        messages = self._merge_consecutive_messages(messages)
        
        # Step 3: Filter ultra-low-quality (pure filler)
        if self._is_ultra_low_quality(messages):
            self.stats.ultra_low_quality_filtered += 1
            return None
        
        return {"messages": messages}
    
    def _remove_edited_duplicates(self, messages: List[Dict]) -> List[Dict]:
        """
        Remove duplicate messages where one is marked (edited).
        Keep only the edited version.
        """
        cleaned = []
        i = 0
        
        while i < len(messages):
            msg = messages[i]
            
            # Skip system messages
            if msg["role"] == "system":
                cleaned.append(msg)
                i += 1
                continue
            
            # Check if next message is edited version
            if i + 1 < len(messages):
                next_msg = messages[i + 1]
                
                # Same role and similar content with (edited)
                if (msg["role"] == next_msg["role"] and 
                    "(edited)" in next_msg["content"] and
                    self._is_edited_version(msg["content"], next_msg["content"])):
                    
                    # Keep edited version, skip original
                    cleaned_content = next_msg["content"].replace(" (edited)", "").replace("(edited)", "")
                    cleaned.append({
                        "role": next_msg["role"],
                        "content": cleaned_content.strip()
                    })
                    self.stats.edited_duplicates_removed += 1
                    i += 2  # Skip both
                    continue
            
            cleaned.append(msg)
            i += 1
        
        return cleaned
    
    def _is_edited_version(self, original: str, edited: str) -> bool:
        """Check if edited message is a variant of original."""
        from difflib import SequenceMatcher
        
        # Remove (edited) tag and emojis for comparison
        edited_clean = edited.replace("(edited)", "").strip()
        original_clean = original.strip()
        
        # Remove emojis and punctuation for text comparison
        edited_text = re.sub(r'[^\w\s]', '', edited_clean).lower()
        original_text = re.sub(r'[^\w\s]', '', original_clean).lower()
        
        # Check similarity (allow small changes)
        similarity = SequenceMatcher(None, original_text, edited_text).ratio()
        return similarity > 0.7
    
    def _merge_consecutive_messages(self, messages: List[Dict]) -> List[Dict]:
        """
        Merge consecutive messages from same role.
        
        CRITICAL for fixing multi-message bursts.
        """
        if len(messages) <= 1:
            return messages
        
        merged = [messages[0]]  # Start with system message
        
        for msg in messages[1:]:
            last_msg = merged[-1]
            
            # Merge if same role (and not system)
            if (msg["role"] == last_msg["role"] and 
                msg["role"] != "system"):
                
                # Merge content with newline
                merged[-1] = {
                    "role": last_msg["role"],
                    "content": last_msg["content"] + "\n" + msg["content"]
                }
                self.stats.consecutive_merged += 1
            else:
                merged.append(msg)
        
        return merged
    
    def _is_ultra_low_quality(self, messages: List[Dict]) -> bool:
        """Filter exchanges with ONLY filler words and no substance."""
        # Get assistant messages (skip system)
        assistant_msgs = [m["content"] for m in messages if m["role"] == "assistant"]
        
        if not assistant_msgs:
            return True
        
        # Check if ALL assistant messages are just filler
        all_filler = True
        for content in assistant_msgs:
            # Remove emojis and punctuation
            text = re.sub(r'[^\w\s]', '', content).strip().lower()
            words = text.split()
            
            # If has more than 2 words, not pure filler
            if len(words) > 2:
                all_filler = False
                break
            
            # If all words are NOT in filler set, not pure filler
            if words and not all(w in self.FILLER_ONLY for w in words):
                all_filler = False
                break
        
        return all_filler

# ==========================================
# DATA WRITER
# ==========================================
class DataWriter:
    """Write training data with validation."""
    
    @staticmethod
    def write_jsonl(data: List[Dict], output_path: Path, validate: bool = True):
        if validate:
            data = DataWriter._validate_data(data)
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            for entry in data:
                json.dump(entry, f, ensure_ascii=False)
                f.write('\n')
        
        logger.info(f"Wrote {len(data)} entries to {output_path}")
    
    @staticmethod
    def _validate_data(data: List[Dict]) -> List[Dict]:
        valid_data = []
        
        for i, entry in enumerate(data):
            try:
                assert "messages" in entry
                assert isinstance(entry["messages"], list)
                assert len(entry["messages"]) >= 2
                
                for msg in entry["messages"]:
                    assert "role" in msg and "content" in msg
                    assert msg["role"] in ["system", "user", "assistant"]
                    assert isinstance(msg["content"], str)
                    assert len(msg["content"].strip()) > 0
                
                valid_data.append(entry)
            except AssertionError as e:
                logger.warning(f"Entry {i} invalid: {e}")
                continue
        
        return valid_data
    
    @staticmethod
    def train_val_split(data: List[Dict], val_ratio: float = 0.1) -> Tuple[List[Dict], List[Dict]]:
        import random
        shuffled = data.copy()
        random.seed(42)
        random.shuffle(shuffled)
        
        split_idx = int(len(shuffled) * (1 - val_ratio))
        return shuffled[:split_idx], shuffled[split_idx:]

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    logger.info("=" * 70)
    logger.info("WhatsApp Parser v10 - Industry-Grade with Quality Filters")
    logger.info("=" * 70)
    logger.info(f"Target User: {Config.TARGET_USER_NAME}")
    logger.info(f"Primary Threshold: {Config.PRIMARY_THRESHOLD_HOURS}h")
    logger.info(f"Turn-Aware: {'✅' if Config.ENABLE_TURN_AWARE else '❌'}")
    logger.info(f"Adaptive Context: {'✅' if Config.ENABLE_ADAPTIVE_CONTEXT else '❌'}")
    logger.info(f"Semantic Model: {'✅' if SEMANTIC_AVAILABLE else '❌'}")
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
        return
    
    chat_files = list(Config.INPUT_DIR.glob("*.txt"))
    if not chat_files:
        logger.error(f"No .txt files found in '{Config.INPUT_DIR}'")
        return
    
    logger.info(f"Found {len(chat_files)} chat file(s)")
    logger.info("Processing each file separately with chronological sorting...")
    
    for chat_file in chat_files:
        stats = ProcessingStats()  # Fresh stats per file
        parser = WhatsAppParser(stats)
        processor = EnhancedSemanticProcessor(stats)
        
        try:
            logger.info(f"\n{'='*70}")
            logger.info(f"Processing: {chat_file.name}")
            logger.info(f"{'='*70}")
            
            # Parse single file
            messages = parser.parse_file(chat_file)
            logger.info(f"  ✓ Parsed {len(messages)} valid messages")
            
            file_data = processor.generate_sliding_windows(messages)
            logger.info(f"  ✓ Generated {len(file_data)} training pairs")
            
            if not file_data:
                logger.warning(f"No training data generated from {chat_file.name}")
                continue
            
            # POST-PROCESSING
            logger.info("POST-PROCESSING: Cleaning training pairs...")
            cleaner = TrainingPairCleaner(stats)
            cleaned_data = []
            for entry in file_data:
                cleaned_entry = cleaner.clean_training_pair(entry)
                if cleaned_entry:
                    cleaned_data.append(cleaned_entry)
            
            logger.info(f"Original pairs: {len(file_data)}")
            logger.info(f"After cleaning: {len(cleaned_data)}")
            
            # SORT BY TIMESTAMP (Most Recent First)
            cleaned_data = list(reversed(cleaned_data))
            logger.info("✅ Sorted by timestamp (most recent first)")
            
            # Generate output filenames
            base_name = chat_file.stem
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
            stats_dict = stats.to_dict()
            with open(stats_file, 'w', encoding='utf-8') as f:
                json.dump(stats_dict, f, indent=2, ensure_ascii=False)
            
            # Write rejection log
            if Config.LOG_REJECTED_MESSAGES and stats.rejection_samples:
                with open(rejection_file, 'w', encoding='utf-8') as f:
                    for sample in stats.rejection_samples:
                        json.dump(sample, f, ensure_ascii=False)
                        f.write('\n')
            
            # Display summary
            logger.info(f"\nPROCESSING SUMMARY - {chat_file.name}")
            logger.info("=" * 70)
            logger.info(f"Total messages: {stats.total_messages}")
            logger.info(f"Valid messages: {stats.parsed_messages}")
            logger.info(f"Sessions created: {stats.sessions_created}")
            logger.info(f"Training pairs: {stats.training_pairs_generated}")
            logger.info(f"Quality rejections: {stats_dict['quality_rejections']['total_quality_rejections']}")
            logger.info(f"Data quality rate: {stats_dict['data_quality_rate']*100:.1f}%")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.error(f"Failed to process {chat_file.name}: {e}", exc_info=True)
    
    logger.info(f"\n🚀 All files processed! Upload *_train.jsonl files to Unsloth")
    logger.info("=" * 70)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
    except Exception as e:
        logger.error(f"\nFatal error: {e}", exc_info=True)