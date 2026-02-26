"""
Analizador de Contexto usando LLM

Este módulo usa un LLM para detectar follow-ups y mejorar queries con contexto,
reemplazando las expresiones regulares por comprensión semántica real.
"""

import json
import re
from typing import Dict, Optional, List, Any
from backend.compat.tools import call_llm_prompt, get_active_llm_backend


class LLMContextAnalyzer:
    """Analiza contexto conversacional usando LLM en lugar de regex"""

    def _call(self, prompt: str, timeout: int = 60) -> str:
        """Llama al backend LLM activo y devuelve el texto de respuesta."""
        backend, model = get_active_llm_backend()
        return call_llm_prompt(prompt, backend=backend, model=model, timeout=timeout)
    
    def _build_history_block(self, recent_turns: Optional[List[Dict]], max_response_chars: int = 400) -> str:
        """Construye un bloque de historial legible para incluir en prompts."""
        if not recent_turns:
            return ""
        lines = []
        for turn in recent_turns:
            q = turn.get("user_query", "").strip()
            r = turn.get("response", "").strip()
            if r and len(r) > max_response_chars:
                r = r[:max_response_chars] + "..."
            lines.append(f"Usuario: {q}")
            if r:
                lines.append(f"Asistente: {r}")
            lines.append("")
        return "\n".join(lines).strip()

    def analyze_and_enhance_query(
        self,
        current_query: str,
        recent_turns: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        En UNA sola llamada LLM determina si la query es follow-up Y,
        si lo es, la reescribe con el contexto necesario.

        Returns:
            {
                "is_follow_up": bool,
                "enhanced_query": str,   # igual a current_query si no es follow-up
                "confidence": float,
                "changes_made": list[str]
            }
        """
        if not recent_turns:
            return {
                "is_follow_up": False,
                "enhanced_query": current_query,
                "confidence": 1.0,
                "changes_made": []
            }

        history_block = self._build_history_block(recent_turns, max_response_chars=600)

        if not history_block:
            return {
                "is_follow_up": False,
                "enhanced_query": current_query,
                "confidence": 1.0,
                "changes_made": []
            }

        prompt = f"""Analiza la siguiente pregunta en el contexto del historial de conversación.

Historial (del más antiguo al más reciente):
{history_block}

Pregunta actual del usuario:
{current_query}

Tarea:
1. Decide si la pregunta actual es un follow-up que necesita contexto previo para entenderse correctamente (referencias implícitas, pronombres sin antecedente, preguntas muy cortas, etc.).
2. Si ES follow-up, reescribe la pregunta añadiendo el contexto implícito del historial para que sea autocontenida.
3. Si NO es follow-up (pregunta ya clara e independiente), devuelve la pregunta original tal cual.

Ejemplos:
- Historial habla de "Caras Nuevas", usuario pregunta "¿quién lo presentaba?" → follow-up → "¿Quién presentaba Caras Nuevas?"
- Historial habla de programas de 1962, usuario pregunta "¿Y en televisión?" → follow-up → "¿Qué programas de televisión había en 1962?"
- Usuario pregunta "busca anuncios de Coca-Cola en 1975" → NO follow-up → devolver igual

Reglas:
- Solo añade contexto que esté EXPLÍCITAMENTE en el historial
- No inventes información
- Mantén el estilo original de la pregunta

Responde SOLO con JSON válido (sin markdown):
{{
    "is_follow_up": true/false,
    "enhanced_query": "pregunta reescrita o la misma si no es follow-up",
    "confidence": 0.0-1.0,
    "changes_made": ["descripción de cambios, o lista vacía si no es follow-up"]
}}"""

        try:
            response_text = self._call(prompt, timeout=60)
            response_text = self._extract_json(response_text)
            result = json.loads(response_text)
            # Asegurar campos obligatorios
            result.setdefault("is_follow_up", False)
            result.setdefault("enhanced_query", current_query)
            result.setdefault("confidence", 0.0)
            result.setdefault("changes_made", [])
            print(f"🤖 Análisis LLM: follow-up={result['is_follow_up']} (confianza={result['confidence']:.2f}) → '{result['enhanced_query']}'")
            return result
        except Exception as e:
            print(f"⚠️ Error en analyze_and_enhance_query: {e}")
            return {
                "is_follow_up": False,
                "enhanced_query": current_query,
                "confidence": 0.0,
                "changes_made": []
            }

    def is_contextual_follow_up(
        self,
        current_query: str,
        last_query: Optional[str] = None,
        last_response: Optional[str] = None,
        recent_turns: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        Determina si una query es un follow-up contextual usando LLM
        
        Args:
            current_query: Query actual del usuario
            last_query: Query anterior (para compatibilidad, ignorado si recent_turns viene)
            last_response: Respuesta anterior (para compatibilidad, ignorado si recent_turns viene)
            recent_turns: Lista de turnos recientes [{user_query, response}, ...]
            
        Returns:
            Dict con:
                - is_follow_up: bool
                - confidence: float (0-1)
                - type: str (expanding, narrowing, clarifying, switching, none)
                - reasoning: str (explicación)
        """
        # Construir historial: si tenemos turnos recientes úsalos; si no, usar last_query/last_response
        if recent_turns:
            history_block = self._build_history_block(recent_turns)
        elif last_query:
            history_block = f"Usuario: {last_query}"
            if last_response:
                history_block += f"\nAsistente: {last_response[:400]}..."
        else:
            return {
                "is_follow_up": False,
                "confidence": 1.0,
                "type": "none",
                "reasoning": "No hay conversación previa"
            }

        if not history_block:
            return {
                "is_follow_up": False,
                "confidence": 1.0,
                "type": "none",
                "reasoning": "No hay conversación previa"
            }

        # Construir prompt para el LLM
        prompt = f"""Analiza si la siguiente pregunta es un follow-up (pregunta de seguimiento) que necesita contexto de la conversación anterior.

Historial de conversación (del más antiguo al más reciente):
{history_block}

Nueva pregunta del usuario:
{current_query}

Determina:
1. ¿Es un follow-up que necesita contexto de la pregunta anterior? (sí/no)
2. Nivel de confianza (0.0 a 1.0)
3. Tipo de follow-up:
   - "expanding": Añade un nuevo aspecto (ej: "¿Y en televisión?")
   - "narrowing": Refina/filtra (ej: "Solo los de 1962")
   - "clarifying": Aclara o pregunta sobre lo anterior (ej: "¿Qué tipo era?", "¿Cuántos había?")
   - "switching": Cambia completamente de tema
   - "none": No es un follow-up

IMPORTANTE:
- Responde SOLO con JSON válido.
- Usa comillas dobles en todas las claves y valores de texto.
- No añadas texto fuera del JSON.

Responde SOLO con JSON válido (sin markdown, sin explicaciones adicionales):
{{
    "is_follow_up": true/false,
    "confidence": 0.0-1.0,
    "type": "expanding/narrowing/clarifying/switching/none",
    "reasoning": "breve explicación"
}}"""

        try:
            response_text = self._call(prompt, timeout=60)
            response_text = self._extract_json(response_text)
            try:
                analysis = json.loads(response_text)
                if not all(k in analysis for k in ["is_follow_up", "confidence", "type"]):
                    raise ValueError("JSON incompleto")
                return analysis
            except (json.JSONDecodeError, ValueError) as e:
                print(f"⚠️ Error parseando respuesta LLM: {e}")
                repaired_text = self._repair_json_response(response_text)
                if repaired_text:
                    try:
                        analysis = json.loads(repaired_text)
                        if not all(k in analysis for k in ["is_follow_up", "confidence", "type"]):
                            raise ValueError("JSON reparado incompleto")
                        return analysis
                    except (json.JSONDecodeError, ValueError) as repair_error:
                        print(f"⚠️ Error parseando JSON reparado: {repair_error}")
                return self._fallback_analysis(current_query, last_query or "")
        except Exception as e:
            print(f"⚠️ Error llamando a LLM: {str(e)}")
            return self._fallback_analysis(current_query, last_query or "")
    
    def enhance_query_with_context(
        self,
        current_query: str,
        conversation_context: Dict[str, Any],
        last_query: Optional[str] = None,
        last_response: Optional[str] = None,
        recent_turns: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        Mejora una query con contexto usando LLM
        
        Args:
            current_query: Query actual del usuario
            conversation_context: Contexto de la conversación
            last_query: Query anterior (ignorado si recent_turns viene)
            last_response: Respuesta anterior (ignorado si recent_turns viene)
            recent_turns: Lista de turnos recientes [{user_query, response}, ...]
            
        Returns:
            Dict con:
                - enhanced_query: str (query mejorada)
                - confidence: float
                - changes_made: List[str] (qué se añadió)
        """
        # Construir bloque de historial
        if recent_turns:
            history_block = self._build_history_block(recent_turns, max_response_chars=600)
        elif last_query:
            history_block = f"Usuario: {last_query}"
            if last_response:
                history_block += f"\nAsistente: {last_response[:600]}"
        else:
            history_block = "Sin conversación previa"

        # Construir prompt
        prompt = f"""Mejora la siguiente pregunta del usuario añadiendo contexto implícito del historial de conversación.

Historial de conversación (del más antiguo al más reciente):
{history_block}

Pregunta actual del usuario:
{current_query}

Tarea: Analiza si la pregunta actual hace referencia implícita a algo de la conversación anterior. Si es así, reescríbela añadiendo el contexto necesario.

Ejemplos:
- Si pregunta anterior fue sobre "Caras Nuevas" y ahora pregunta "¿Qué tipo de programa era?", responde: "¿Qué tipo de programa era Caras Nuevas?"
- Si pregunta anterior fue sobre "programas de 1962" y ahora pregunta "¿Y en televisión?", responde: "¿Qué programas había en televisión en 1962?"
- Si la pregunta usa "ese programa", "esa persona", etc., reemplázalos con el nombre del contexto

Reglas importantes:
1. Solo añade información que esté EXPLÍCITAMENTE en el contexto o pregunta anterior
2. Si la pregunta usa pronombres (él, ella, ese, esa) o preguntas sobre características sin mencionar el sujeto, SIEMPRE añade el sujeto del contexto
3. Si la pregunta ya es clara y autocontenida, devuélvela igual
4. Mantén el estilo de pregunta original (informal/formal)
5. No inventes información

IMPORTANTE:
- Responde SOLO con JSON válido.
- Usa comillas dobles en todas las claves y valores de texto.
- No añadas texto fuera del JSON.

Responde SOLO con JSON válido (sin markdown, sin bloques de código):
{{
    "enhanced_query": "pregunta mejorada",
    "confidence": 0.0-1.0,
    "changes_made": ["lista de cambios realizados"]
}}"""

        try:
            response_text = self._call(prompt, timeout=60)
            response_text = self._extract_json(response_text)
            try:
                enhancement = json.loads(response_text)
                if "enhanced_query" not in enhancement:
                    raise ValueError("JSON sin enhanced_query")
                return enhancement
            except (json.JSONDecodeError, ValueError) as e:
                print(f"⚠️ Error parseando enhancement: {e}")
                return {"enhanced_query": current_query, "confidence": 0.0, "changes_made": []}
        except Exception as e:
            print(f"⚠️ Error en enhancement LLM: {str(e)}")
            return {"enhanced_query": current_query, "confidence": 0.0, "changes_made": []}
    
    def _extract_json(self, text: str) -> str:
        """
        Extrae JSON de una respuesta que puede contener markdown o texto adicional
        """
        # Intentar encontrar JSON entre ```json y ```
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            return json_match.group(1)
        
        # Buscar el primer { y el último }
        start = text.find('{')
        end = text.rfind('}')
        
        if start != -1 and end != -1 and end > start:
            return text[start:end+1]
        
        return text

    def _repair_json_response(self, text: str) -> Optional[str]:
        """
        Intenta reparar respuestas JSON malformadas (razones sin comillas, incompletas, etc.).
        """
        try:
            # Quitar bloques markdown si quedaron
            cleaned = self._extract_json(text)

            # Si ya es JSON válido, devolverlo
            try:
                json.loads(cleaned)
                return cleaned
            except json.JSONDecodeError:
                pass

            # El JSON está incompleto. Intentar completarlo.
            # Primero, reparar valores de "reasoning" sin cerrar
            # Buscar si hay un "reasoning" sin terminar
            reasoning_match = re.search(r'"reasoning"\s*:\s*"([^"]*?)(?:$|[^"]*$)', cleaned)
            if reasoning_match:
                partial_reasoning = reasoning_match.group(1)
                # Escapar comillas internas si existen
                partial_reasoning = partial_reasoning.replace('"', '\\"')
                # Reemplazar en el JSON
                cleaned = re.sub(
                    r'"reasoning"\s*:\s*"[^"]*',
                    f'"reasoning": "{partial_reasoning}',
                    cleaned
                )

            # Asegurar que el JSON termina correctamente
            cleaned = cleaned.rstrip()
            
            # Contar braces abiertos y cerrados
            open_braces = cleaned.count('{')
            close_braces = cleaned.count('}')
            
            # Si hay braces sin cerrar, agregarlos
            if open_braces > close_braces:
                cleaned += '}' * (open_braces - close_braces)
            
            # Verificar si hay una comilla sin cerrar en reasoning
            if cleaned.count('"reasoning"') > 0:
                # Contar comillas después de "reasoning"
                after_reasoning = cleaned.split('"reasoning":', 1)[1] if '"reasoning":' in cleaned else ""
                # Si termina con una comilla sin cerrar, cerrarla
                if after_reasoning and after_reasoning.count('"') % 2 == 1:
                    # Hay una comilla sin cerrar
                    if not after_reasoning.rstrip().endswith('"'):
                        cleaned = cleaned.rstrip() + '"'
            
            # Intentar parsear nuevamente
            try:
                parsed = json.loads(cleaned)
                return cleaned
            except json.JSONDecodeError as e:
                # Aún hay problemas. Intentar una reparación más agresiva.
                # Buscar el patrón: "reasoning": "...
                # y asegurar que cierre correctamente
                if '"reasoning"' in cleaned and cleaned.count('"') % 2 == 1:
                    # Hay una comilla impar. Cerrar el JSON de forma segura.
                    # Encontrar donde comienza "reasoning" y cortarlo ahí, dejando un texto simple
                    match = re.search(r'"reasoning"\s*:\s*"([^"]*)', cleaned)
                    if match:
                        reasoning_text = match.group(1)
                        # Crear un JSON limpio con los campos básicos
                        basic_json = {
                            "is_follow_up": "true" in cleaned.lower(),
                            "confidence": 0.7,
                            "type": "clarifying",
                            "reasoning": reasoning_text[:100]  # Limitar a 100 caracteres
                        }
                        return json.dumps(basic_json)
                
                return None
                
        except Exception as e:
            print(f"⚠️ Error en reparación de JSON: {e}")
            return None
    
    def _fallback_analysis(self, current_query: str, last_query: Optional[str]) -> Dict[str, Any]:
        """
        Análisis de fallback simple si el LLM falla
        """
        query_lower = current_query.lower().strip()
        
        # Patrones básicos de follow-up
        follow_up_keywords = [
            "y en", "y de", "también", "además", "pero", 
            "qué tipo", "cómo era", "cuántos", "cuándo", "dónde"
        ]
        
        is_follow_up = any(keyword in query_lower for keyword in follow_up_keywords)
        
        # Detectar si es muy corta (probablemente follow-up)
        if len(query_lower.split()) <= 5 and not is_follow_up:
            is_follow_up = True
        
        return {
            "is_follow_up": is_follow_up,
            "confidence": 0.6 if is_follow_up else 0.8,
            "type": "clarifying" if is_follow_up else "none",
            "reasoning": f"Análisis de fallback (LLM no disponible)"
        }


# Instancia global para reutilizar
_llm_analyzer = None

def get_llm_analyzer() -> LLMContextAnalyzer:
    """Obtiene la instancia global del analizador"""
    global _llm_analyzer
    if _llm_analyzer is None:
        _llm_analyzer = LLMContextAnalyzer()
    return _llm_analyzer
