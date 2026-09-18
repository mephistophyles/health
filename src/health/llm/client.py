"""The one module that talks to OpenRouter.

Everything a generation needs happens here: task config lookup, prompt assembly,
structured output, schema validation with a single repair retry, fallback model on a
provider error, the monthly spend cap, and the operational log. Nothing else in the app
makes an outbound model call.
"""
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from psycopg import Connection
from pydantic import BaseModel, ValidationError

from health.settings import Settings

FENCE = re.compile(r'```(?:json)?\s*(.*?)```', re.DOTALL)


class LlmError(RuntimeError):
    """A loop failed. The message is shown to the user next to a retry button."""


class SpendCapReached(LlmError):
    pass


@dataclass
class TaskConfig:
    task: str
    model: str
    fallback_model: str | None
    temperature: float
    max_tokens: int
    timeout_s: int
    prompt_version_id: str
    system_prompt: str
    user_template: str


@dataclass
class Completion:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


def load_task_config(connection: Connection, household_id: str, task: str) -> TaskConfig:
    row = connection.execute(
        """SELECT c.task, c.model, c.fallback_model, c.temperature, c.max_tokens, c.timeout_s,
                  p.id AS prompt_version_id, p.system_prompt, p.user_template
             FROM llm_task_config c
             JOIN prompt_version p ON p.id = c.active_prompt_version_id
            WHERE c.household_id = %s AND c.task = %s""",
        (household_id, task),
    ).fetchone()
    if row is None:
        raise LlmError(f'No model or prompt is configured for {task}')
    return TaskConfig(
        task=row['task'],
        model=row['model'],
        fallback_model=row['fallback_model'],
        temperature=float(row['temperature']),
        max_tokens=row['max_tokens'],
        timeout_s=row['timeout_s'],
        prompt_version_id=str(row['prompt_version_id']),
        system_prompt=row['system_prompt'],
        user_template=row['user_template'],
    )


def render(template: str, context: dict[str, Any]) -> str:
    """Fill `{{name}}` and `{{name.path}}` from the context. Unknown names render empty."""

    def lookup(match: re.Match[str]) -> str:
        value: Any = context
        for part in match.group(1).strip().split('.'):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return ''
        if isinstance(value, str):
            return value
        return json.dumps(value, indent=2, default=str)

    return re.sub(r'\{\{([^}]+)\}\}', lookup, template)


def month_spend(connection: Connection, household_id: str) -> float:
    row = connection.execute(
        """SELECT COALESCE(SUM(cost_usd), 0) AS spend FROM llm_call
            WHERE household_id = %s AND created_at >= date_trunc('month', now())""",
        (household_id,),
    ).fetchone()
    return float(row['spend'])


def spend_cap(connection: Connection, household_id: str) -> float:
    row = connection.execute(
        "SELECT value FROM setting WHERE household_id = %s AND key = 'monthly_spend_cap_usd'",
        (household_id,),
    ).fetchone()
    return float(row['value']) if row else 20.0


def extract_json(text: str) -> dict[str, Any]:
    """Models wrap JSON in prose or fences more often than they should."""
    candidate = text.strip()
    fenced = FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    else:
        start, end = candidate.find('{'), candidate.rfind('}')
        if start != -1 and end > start:
            candidate = candidate[start : end + 1]
    return json.loads(candidate)


class OpenRouterClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        if not self.settings.openrouter_api_key:
            raise LlmError('OPENROUTER_API_KEY is not set; generation is unavailable')
        headers = {
            'Authorization': f'Bearer {self.settings.openrouter_api_key}',
            'Content-Type': 'application/json',
            'X-Title': 'Health',
        }
        if self.settings.openrouter_referer:
            headers['HTTP-Referer'] = self.settings.openrouter_referer
        return headers

    def complete(
        self,
        config: TaskConfig,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None,
        model: str | None = None,
    ) -> Completion:
        model = model or config.model
        body: dict[str, Any] = {
            'model': model,
            'messages': messages,
            'temperature': config.temperature,
            'max_tokens': config.max_tokens,
            'usage': {'include': True},
        }
        if schema is not None:
            body['response_format'] = {
                'type': 'json_schema',
                'json_schema': {'name': 'plan', 'strict': False, 'schema': schema},
            }

        started = time.monotonic()
        try:
            response = httpx.post(
                f'{self.settings.openrouter_base_url}/chat/completions',
                headers=self._headers(),
                json=body,
                timeout=config.timeout_s,
            )
        except httpx.TimeoutException as error:
            raise LlmError(f'{model} timed out after {config.timeout_s}s') from error
        except httpx.HTTPError as error:
            raise LlmError(f'{model} could not be reached: {error}') from error

        if response.status_code == 400 and 'response_format' in response.text and schema:
            # The model does not support structured outputs. The schema is in the prompt too.
            return self.complete(config, messages, None, model=model)
        if response.status_code >= 400:
            raise LlmError(f'{model} returned {response.status_code}: {response.text[:300]}')

        payload = response.json()
        choices = payload.get('choices') or []
        if not choices:
            raise LlmError(f'{model} returned no choices')
        usage = payload.get('usage') or {}
        return Completion(
            text=choices[0].get('message', {}).get('content') or '',
            model=payload.get('model', model),
            tokens_in=int(usage.get('prompt_tokens') or 0),
            tokens_out=int(usage.get('completion_tokens') or 0),
            cost_usd=float(usage.get('cost') or 0.0),
            latency_ms=int((time.monotonic() - started) * 1000),
            raw=payload,
        )


@dataclass
class Result:
    value: BaseModel
    model: str
    prompt_version_id: str
    repaired: bool


def _log_call(
    connection: Connection,
    household_id: str,
    config: TaskConfig,
    completion: Completion | None,
    status: str,
    request_body: dict[str, Any],
    error: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO llm_call (household_id, task, model, prompt_version_id, tokens_in,
                                 tokens_out, cost_usd, latency_ms, status, error, request, response)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            household_id,
            config.task,
            completion.model if completion else config.model,
            config.prompt_version_id,
            completion.tokens_in if completion else 0,
            completion.tokens_out if completion else 0,
            completion.cost_usd if completion else 0,
            completion.latency_ms if completion else 0,
            status,
            error,
            json.dumps(request_body, default=str),
            json.dumps({'text': completion.text} if completion else None, default=str),
        ),
    )


def run_task(
    connection: Connection,
    settings: Settings,
    household_id: str,
    task: str,
    context: dict[str, Any],
    model_type: type[BaseModel],
    schema: dict[str, Any] | None = None,
    post_validate: Callable[[BaseModel], list[str]] | None = None,
) -> Result:
    """Assemble, call, validate, and log. Raises `LlmError` when the loop cannot succeed."""
    config = load_task_config(connection, household_id, task)
    cap = spend_cap(connection, household_id)
    spent = month_spend(connection, household_id)
    if spent >= cap:
        raise SpendCapReached(
            f'This month has used ${spent:.2f} of the ${cap:.2f} cap. '
            'Raise the cap in Settings to generate again.'
        )

    schema = schema if schema is not None else model_type.model_json_schema()
    user_prompt = render(config.user_template, {**context, 'schema': json.dumps(schema)})
    messages = [
        {'role': 'system', 'content': config.system_prompt},
        {'role': 'user', 'content': user_prompt},
    ]
    request_body = {'task': task, 'messages': messages}

    client = OpenRouterClient(settings)
    models = [config.model] + ([config.fallback_model] if config.fallback_model else [])
    completion: Completion | None = None
    last_error: Exception | None = None

    for model in models:
        try:
            completion = client.complete(config, messages, schema, model=model)
            break
        except LlmError as error:
            last_error = error
            _log_call(connection, household_id, config, None, 'error', request_body, str(error))
    if completion is None:
        raise LlmError(str(last_error))

    repaired = False
    for attempt in range(2):
        problems: list[str] = []
        value: BaseModel | None = None
        try:
            value = model_type.model_validate(extract_json(completion.text))
        except (ValidationError, json.JSONDecodeError) as error:
            problems = [str(error)]
        if value is not None and post_validate is not None:
            problems = post_validate(value)

        if not problems:
            _log_call(
                connection, household_id, config, completion,
                'repaired' if repaired else 'ok', request_body,
            )
            return Result(
                value=value,
                model=completion.model,
                prompt_version_id=config.prompt_version_id,
                repaired=repaired,
            )

        if attempt == 1:
            _log_call(
                connection, household_id, config, completion, 'invalid_json',
                request_body, '\n'.join(problems)[:2000],
            )
            raise LlmError(
                f'{completion.model} did not return a usable plan:\n' + '\n'.join(problems[:10])
            )

        # One repair retry, with the errors handed back (PRD 11).
        repaired = True
        messages = messages + [
            {'role': 'assistant', 'content': completion.text},
            {
                'role': 'user',
                'content': 'That response has problems. Fix exactly these and return the '
                'corrected JSON only, with nothing else changed:\n' + '\n'.join(problems[:20]),
            },
        ]
        completion = client.complete(config, messages, schema, model=completion.model)
    raise LlmError('unreachable')
