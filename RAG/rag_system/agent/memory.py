from typing import List
import tiktoken
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, trim_messages
from langchain_openai import ChatOpenAI

from ..core.prompts import SUMMARY_PROMPT_TEMPLATE

class ConversationSummarizer:
    """Manages conversation history by summarizing older messages when a threshold is reached."""
    
    def __init__(self, llm: ChatOpenAI, summary_threshold: int = 10):
        self.llm = llm
        # Threshold can now be used to trigger the check, but trim_messages uses max_tokens.
        # We'll keep this for the initial guard clause (message count check is fast).
        self.threshold = summary_threshold
        try:
            self.encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self.encoding = None

    def _count_tokens(self, messages: List[BaseMessage]) -> int:
        """Count tokens precisely using tiktoken."""
        if not self.encoding:
            # Fallback approximation: 4 chars per token
            return sum(len(msg.content) // 4 for msg in messages)
        
        num_tokens = 0
        for msg in messages:
            # Simplified logic: add tokens for content. 
            # Real ChatML has overhead (role, etc.), but this is sufficient for trimming.
            num_tokens += len(self.encoding.encode(msg.content))
        return num_tokens

    def _format_message(self, msg: BaseMessage) -> str:
        """Format a single message for summarization with truncation for tools."""
        role_map = {
            "human": "User",
            "ai": "AI",
            "tool": "Tool",
            "system": "System"
        }
        # Default to capitalized type if not in map
        role = role_map.get(msg.type, msg.type.capitalize())
        
        content = msg.content
        # Truncate long tool outputs to avoid wasting summary context window
        if msg.type == "tool" and len(content) > 500:
            content = content[:500] + "... (truncated)"
            
        return f"{role}: {content}"

    def process_messages(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        """
        Compresses the message history if it exceeds the threshold.
        Returns a list containing a summary system message and recent messages.
        """
        # 1. Quick check (Guard Clause) - keep using message count for speed
        if len(messages) <= self.threshold:
            return messages

        # 2. Keep recent messages using official trim_messages with TOKEN counting
        # We aim to keep the last ~3000 tokens of context (adjust based on model window)
        recent_messages = trim_messages(
            messages,
            max_tokens=3000, 
            strategy="last",
            token_counter=self._count_tokens, 
            include_system=False, # We handle system prompts manually in the node
            start_on="human",     # Ensure conversation cut starts naturally with a user query
            allow_partial=False
        )

        # 3. Identify history to summarize
        # Since trim_messages returns a new list, we need to find the split point.
        # Simple approach: The messages NOT in recent_messages are history.
        # Assumption: trim_messages returns a suffix of the original list (identity check might fail if copies are made, but usually objects are same).
        # Safer to rely on counts if no copies made, but let's just slice based on length.
        num_to_keep = len(recent_messages)
        if num_to_keep == 0:
            # Should not happen unless max_tokens is tiny
            return messages
            
        # If we kept everything (because they fit in max_tokens), no need to summarize
        if num_to_keep == len(messages):
            return messages
            
        history_to_summarize = messages[:-num_to_keep]
        
        if not history_to_summarize:
            return messages

        # 4. Generate Summary using structured prompt
        conversation_text = "\n".join(
            self._format_message(msg) for msg in history_to_summarize
        )

        try:
            # Invoke LLM to generate summary
            response = self.llm.invoke([
                SystemMessage(content=SUMMARY_PROMPT_TEMPLATE),
                HumanMessage(content=conversation_text)
            ])
            summary = response.content
            
            # 5. Return [Summary Context] + [Recent Messages]
            return [SystemMessage(content=f"【先前對話摘要】\n{summary}")] + recent_messages

        except Exception:
            # Fallback: just return trimmed messages if summarization fails
            return recent_messages