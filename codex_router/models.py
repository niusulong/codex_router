"""Pydantic models for Responses API request parsing."""

from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field

# Type aliases
InputContentPart = Union["InputTextContent", "InputImageContent"]


class InputTextContent(BaseModel):
    type: Literal["input_text", "output_text"] = "input_text"
    text: str
    annotations: list[Any] | None = None


class InputImageContent(BaseModel):
    type: Literal["input_image"] = "input_image"
    image_url: str | None = None
    file_id: str | None = None
    detail: str | None = None


class InputMessageItem(BaseModel):
    type: Literal["message"] = "message"
    role: str
    content: str | list[InputContentPart]


class InputFunctionCallItem(BaseModel):
    type: Literal["function_call"] = "function_call"
    call_id: str
    name: str
    arguments: str


class InputFunctionCallOutputItem(BaseModel):
    type: Literal["function_call_output"] = "function_call_output"
    call_id: str
    output: str


InputItem = Union[InputMessageItem, InputFunctionCallItem, InputFunctionCallOutputItem]


class TextFormat(BaseModel):
    type: str
    json_schema: dict[str, Any] | None = None


class ResponsesRequest(BaseModel):
    model: str
    input: str | list[InputItem]
    instructions: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    max_output_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    stream: bool | None = None
    text: TextFormat | dict[str, Any] | None = None
    previous_response_id: str | None = None

    model_config = {"extra": "allow"}
