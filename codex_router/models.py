from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


# --- Content parts within a message's content array ---


class InputTextContent(BaseModel):
    type: Literal["input_text"] = "input_text"
    text: str


class InputImageContent(BaseModel):
    type: Literal["input_image"] = "input_image"
    image_url: Optional[str] = None
    file_id: Optional[str] = None
    detail: Optional[str] = None


InputContentPart = Union[InputTextContent, InputImageContent]


# --- Items within the input array ---


class InputMessageItem(BaseModel):
    type: Literal["message"] = "message"
    role: Literal["user", "assistant", "system", "developer"]
    content: Union[str, list[InputContentPart]]


class InputFunctionCallItem(BaseModel):
    type: Literal["function_call"] = "function_call"
    id: Optional[str] = None
    call_id: str
    name: str
    arguments: str


class InputFunctionCallOutputItem(BaseModel):
    type: Literal["function_call_output"] = "function_call_output"
    call_id: str
    output: str


InputItem = Union[
    InputFunctionCallItem,
    InputFunctionCallOutputItem,
    InputMessageItem,
]


# --- Tools ---


class FunctionToolParam(BaseModel):
    type: Literal["function"] = "function"
    name: str
    description: Optional[str] = None
    parameters: Optional[dict[str, Any]] = None
    strict: Optional[bool] = None


# --- Tool choice ---


class FunctionToolChoice(BaseModel):
    type: Literal["function"] = "function"
    name: str


ToolChoice = Union[Literal["auto", "none", "required"], FunctionToolChoice, str]


# --- Text format ---


class TextFormat(BaseModel):
    type: str
    json_schema: Optional[dict[str, Any]] = None


# --- Top-level Responses API request ---


class ResponsesRequest(BaseModel):
    model: str
    input: Union[str, list[InputItem]]
    instructions: Optional[str] = None
    tools: Optional[list[Any]] = None
    tool_choice: Optional[ToolChoice] = None
    max_output_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stream: Optional[bool] = None
    text: Optional[TextFormat] = None
    previous_response_id: Optional[str] = None

    model_config = {"extra": "allow"}
