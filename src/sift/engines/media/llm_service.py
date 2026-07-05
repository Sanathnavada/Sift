import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class LLMCleaner:
    """
    Dedicated service for cleaning noisy OCR/Audio text using an LLM.
    Defaults to the Ollama setup seen in the Telegram agent.
    """
    def __init__(self):
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        self.api_key = os.getenv("OLLAMA_API_KEY", "ollama")
        # Using a default model, but it can be overridden by env vars
        self.model = os.getenv("CLEANER_MODEL", "gpt-oss:20b-cloud")
        
        try:
            from openai import OpenAI

            self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)
            self.is_configured = True
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}")
            self.is_configured = False

        self.system_prompt = (
            "You are an expert copy editor and data cleaner. Your job is to take raw OCR "
            "text extracted from Instagram images/carousels or rough audio transcripts, and clean it into a highly readable format.\n\n"
            "RULES:\n"
            "1. Remove UI artifacts: usernames (e.g., 'jeremielotemo'), timestamps (e.g., '1m', '4/20/25'), and pagination ('1/6', '2/6').\n You must only retain the post links for exaample 'Post: https://www.instagram.com/p/DUixIluCDjl/' this link has to be placed at the begginning."
            "2. Remove duplicate text or recurring headers that appear on every slide.\n"
            "3. Clean up excessive hashtags, keeping only the core message.\n"
            "4. Fix OCR typos and format into clean paragraphs or bullet points where appropriate.\n"
            "5. DO NOT add any conversational filler. Do not start with 'Here is the cleaned text'. Just output the final cleaned text."
            
        )
        

    def clean_text(self, raw_text: str) -> str:
        if not self.is_configured or not raw_text.strip():
            return raw_text

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"Clean the following raw extraction:\n\n{raw_text}"}
                ],
                temperature=0.3, # Low temperature for high fidelity/consistency
                max_tokens=2048
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"LLM Cleaning failed: {e}. Falling back to raw text.")
            return raw_text

    def clean_bulk_file(self, filepath: str, output_filepath: str):
        """Processes a bulk text file separated by === lines."""
        if not os.path.exists(filepath):
            logger.error(f"File not found: {filepath}")
            return

        delimiter = "=" * 80
        logger.info(f"Starting bulk clean on {filepath}")
        
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Split by the exact delimiter used in the pipeline
        posts = [post.strip() for post in content.split(delimiter) if post.strip()]
        
        with open(output_filepath, 'w', encoding='utf-8') as out_f:
            for i, post in enumerate(posts, 1):
                logger.info(f"Cleaning post {i}/{len(posts)}...")
                cleaned = self.clean_text(post)
                
                out_f.write(cleaned + "\n")
                out_f.write(delimiter + "\n\n")
        
        logger.info(f"Bulk clean complete. Saved to {output_filepath}")