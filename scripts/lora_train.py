#!/usr/bin/env python3
"""
Phase 5: LoRA Fine-Tuning Script for LessonLens

Fine-tunes a base model (Qwen2.5) with LoRA adapters using lesson summary
training data exported from the API.

Prerequisites:
    pip install transformers peft datasets accelerate bitsandbytes

Usage:
    # Export training data first:
    curl -X POST http://localhost:5001/api/fine-tune/export/jsonl \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -o training-data.jsonl

    # Run training:
    python scripts/lora_train.py \
        --data training-data.jsonl \
        --base-model Qwen/Qwen2.5-7B-Instruct \
        --output ./lora-adapters/lessonlens-qwen7b \
        --epochs 3 \
        --rank 16

    # Create Ollama Modelfile and load:
    python scripts/lora_train.py --create-modelfile \
        --base-model qwen2.5:7b \
        --adapter-path ./lora-adapters/lessonlens-qwen7b \
        --output ./Modelfile.lessonlens
"""
import argparse
import json
import os
import sys


def load_training_data(path):
    """Load JSONL training data exported from the API."""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    print(f"Loaded {len(records)} training records from {path}")
    return records


def train_lora(data_path, base_model, output_dir, epochs=3, rank=16,
               alpha=32, lr=2e-4, batch_size=4, max_length=4096,
               api_url=None, run_id=None, token=None):
    """Run LoRA fine-tuning."""
    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, get_peft_model, TaskType
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            TrainingArguments,
            Trainer,
            DataCollatorForSeq2Seq,
        )
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Install: pip install transformers peft datasets accelerate bitsandbytes torch")
        sys.exit(1)

    # Update API run status
    def update_run(status=None, metrics=None, error=None):
        if not api_url or not run_id or not token:
            return
        import urllib.request
        body = {}
        if status:
            body["status"] = status
        if metrics:
            body["metrics"] = metrics
        if error:
            body["error_message"] = error
        req = urllib.request.Request(
            f"{api_url}/api/fine-tune/runs/{run_id}",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="PUT",
        )
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print(f"Warning: could not update run: {e}")

    update_run(status="running")

    try:
        records = load_training_data(data_path)
        if not records:
            raise ValueError("No training records found")

        # Load tokenizer and model
        print(f"Loading base model: {base_model}")
        tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        model = AutoModelForCausalLM.from_pretrained(
            base_model,
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            device_map="auto" if torch.cuda.is_available() else None,
            trust_remote_code=True,
        )

        # Configure LoRA
        lora_config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=rank,
            lora_alpha=alpha,
            lora_dropout=0.05,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                            "gate_proj", "up_proj", "down_proj"],
            bias="none",
        )

        model = get_peft_model(model, lora_config)
        model.print_trainable_parameters()

        # Prepare dataset
        def format_record(record):
            messages = record["messages"]
            text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
            return {"text": text}

        dataset = Dataset.from_list(records)
        dataset = dataset.map(format_record)

        def tokenize(example):
            result = tokenizer(
                example["text"],
                truncation=True,
                max_length=max_length,
                padding=False,
            )
            result["labels"] = result["input_ids"].copy()
            return result

        dataset = dataset.map(tokenize, remove_columns=["text", "messages"])

        # Training arguments
        os.makedirs(output_dir, exist_ok=True)
        training_args = TrainingArguments(
            output_dir=output_dir,
            num_train_epochs=epochs,
            per_device_train_batch_size=batch_size,
            gradient_accumulation_steps=max(1, 8 // batch_size),
            learning_rate=lr,
            fp16=torch.cuda.is_available(),
            logging_steps=10,
            save_strategy="epoch",
            save_total_limit=2,
            report_to="none",
            remove_unused_columns=False,
        )

        trainer = Trainer(
            model=model,
            args=training_args,
            train_dataset=dataset,
            data_collator=DataCollatorForSeq2Seq(tokenizer, padding=True),
        )

        print(f"Starting LoRA training: {epochs} epochs, rank={rank}, lr={lr}")
        result = trainer.train()

        # Save adapter
        model.save_pretrained(output_dir)
        tokenizer.save_pretrained(output_dir)

        metrics = {
            "train_loss": result.training_loss,
            "train_runtime": result.metrics.get("train_runtime", 0),
            "train_samples": len(dataset),
            "epochs": epochs,
            "rank": rank,
        }

        print(f"\nTraining complete! Adapter saved to: {output_dir}")
        print(f"Loss: {result.training_loss:.4f}")

        update_run(
            status="completed",
            metrics=metrics,
        )

        # Also update record count and output path
        if api_url and run_id and token:
            import urllib.request
            body = {
                "training_records": len(records),
                "output_path": os.path.abspath(output_dir),
            }
            req = urllib.request.Request(
                f"{api_url}/api/fine-tune/runs/{run_id}",
                data=json.dumps(body).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                method="PUT",
            )
            try:
                urllib.request.urlopen(req, timeout=10)
            except Exception:
                pass

        return metrics

    except Exception as e:
        print(f"Training failed: {e}", file=sys.stderr)
        update_run(status="failed", error=str(e))
        raise


def create_modelfile(base_model, adapter_path, output_path, model_name=None):
    """Create an Ollama Modelfile that loads the LoRA adapter."""
    if not model_name:
        model_name = f"lessonlens-{base_model.replace(':', '-')}"

    content = f"""FROM {base_model}
ADAPTER {os.path.abspath(adapter_path)}

PARAMETER temperature 0.3
PARAMETER num_ctx 8192

SYSTEM You are a Mandarin Chinese lesson summarizer. Given a chat transcript between a teacher and student, produce a structured JSON lesson summary following the lesson-data.v1 schema.
"""

    with open(output_path, "w") as f:
        f.write(content)

    print(f"Modelfile written to: {output_path}")
    print(f"To create the model in Ollama, run:")
    print(f"  ollama create {model_name} -f {output_path}")
    return model_name


def main():
    parser = argparse.ArgumentParser(description="LessonLens LoRA Fine-Tuning")

    # Training mode
    parser.add_argument("--data", help="Path to JSONL training data")
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-7B-Instruct",
                        help="HuggingFace model ID or Ollama model name")
    parser.add_argument("--output", default="./lora-adapters/lessonlens",
                        help="Output directory for adapter")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--rank", type=int, default=16, help="LoRA rank")
    parser.add_argument("--alpha", type=int, default=32, help="LoRA alpha")
    parser.add_argument("--lr", type=float, default=2e-4, help="Learning rate")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=4096)

    # API integration
    parser.add_argument("--api-url", default="http://localhost:5001",
                        help="LessonLens API URL for status updates")
    parser.add_argument("--run-id", type=int, help="Fine-tune run ID to update")
    parser.add_argument("--token", help="JWT token for API auth")

    # Modelfile mode
    parser.add_argument("--create-modelfile", action="store_true",
                        help="Create Ollama Modelfile instead of training")
    parser.add_argument("--adapter-path", help="Path to trained adapter")
    parser.add_argument("--model-name", help="Name for the Ollama model")

    args = parser.parse_args()

    if args.create_modelfile:
        if not args.adapter_path:
            parser.error("--adapter-path required for --create-modelfile")
        create_modelfile(args.base_model, args.adapter_path, args.output, args.model_name)
    elif args.data:
        train_lora(
            data_path=args.data,
            base_model=args.base_model,
            output_dir=args.output,
            epochs=args.epochs,
            rank=args.rank,
            alpha=args.alpha,
            lr=args.lr,
            batch_size=args.batch_size,
            max_length=args.max_length,
            api_url=args.api_url,
            run_id=args.run_id,
            token=args.token,
        )
    else:
        parser.print_help()
        print("\nExamples:")
        print("  python scripts/lora_train.py --data training-data.jsonl --epochs 3")
        print("  python scripts/lora_train.py --create-modelfile --adapter-path ./lora-adapters/lessonlens --base-model qwen2.5:7b --output Modelfile")


if __name__ == "__main__":
    main()
