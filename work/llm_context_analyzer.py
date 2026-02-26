"""
Analizador de Contexto usando LLM

Este módulo usa un LLM para detectar follow-ups y mejorar queries con contexto,
reemplazando las expresiones regulares por comprensión semántica real.
"""

import json
import re
import requests
from typing import Dict, Optional, List, Any
from config import OLLAMA_BASE_URL, OLLAMA_LLM_MODEL


class LLMContextAnalyzer:
    """Analiza contexto conversacional usando LLM en lugar de regex"""
    
    def __init__(self, ollama_base_url: str = None, model: str = None):
        """
        Inicializa el analizador con conexión a Ollama
        
        Args:
            ollama_base_url: URL base de Ollama
            model: Modelo a usar
        """
        self.base_url = ollama_base_url or OLLAMA_BASE_URL
        self.model = model or OLLAMA_LLM_MODEL
    
    def is_contextual_follow_up(
        self,
        current_query: str,
        last_query: Optional[str] = None,
        last_response: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Determina si una query es un follow-up contextual usando LLM
        
        Args:
            current_query: Query actual del usuario
            last_query: Query anterior (opcional)
            last_response: Respuesta anterior (opcional)
            
        Returns:
            Dict con:
                - is_follow_up: bool
                - confidence: float (0-1)
                - type: str (expanding, narrowing, clarifying, switching, none)
                - reasoning: str (explicación)
        """
        if not last_query:
            return {
                "is_follow_up": False,
                "confidence": 1.0,
                "type": "none",
                "reasoning": "No hay conversación previa"
            }
        
        # Construir prompt para el LLM
        prompt = f"""Analiza si la siguiente pregunta es un follow-up (pregunta de seguimiento) que necesita contexto de la conversación anterior.

Conversación anterior:
Usuario: {last_query}
{f"Asistente: {last_response[:200]}..." if last_response else ""}

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
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "temperature": 0.1,  # Baja temperatura para respuestas más consistentes
                },
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                response_text = result.get("response", "").strip()
                
                # Extraer JSON de la respuesta
                response_text = self._extract_json(response_text)
                
                try:
                    analysis = json.loads(response_text)
                    
                    # Validar campos
                    if not all(k in analysis for k in ["is_follow_up", "confidence", "type"]):
                        raise ValueError("JSON incompleto")
                    
                    return analysis
                    
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"⚠️ Error parseando respuesta LLM: {e}")
                    print(f"Respuesta recibida: {response_text}")
                    # Intentar reparar JSON malformado
                    repaired_text = self._repair_json_response(response_text)
                    if repaired_text:
                        try:
                            analysis = json.loads(repaired_text)
                            if not all(k in analysis for k in ["is_follow_up", "confidence", "type"]):
                                raise ValueError("JSON reparado incompleto")
                            return analysis
                        except (json.JSONDecodeError, ValueError) as repair_error:
                            print(f"⚠️ Error parseando JSON reparado: {repair_error}")
                    # Fallback: analizar manualmente la respuesta
                    return self._fallback_analysis(current_query, last_query)
            else:
                print(f"⚠️ Error en request a Ollama: {response.status_code}")
                return self._fallback_analysis(current_query, last_query)
                
        except Exception as e:
            print(f"⚠️ Error llamando a LLM: {str(e)}")
            return self._fallback_analysis(current_query, last_query)
    
    def enhance_query_with_context(
        self,
        current_query: str,
        conversation_context: Dict[str, Any],
        last_query: Optional[str] = None,
        last_response: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Mejora una query con contexto usando LLM
        
        Args:
            current_query: Query actual del usuario
            conversation_context: Contexto de la conversación
            last_query: Query anterior
            
        Returns:
            Dict con:
                - enhanced_query: str (query mejorada)
                - confidence: float
                - changes_made: List[str] (qué se añadió)
        """
        # Extraer información relevante del contexto
        context_summary = conversation_context.get("context_summary", {})
        global_entities = conversation_context.get("global_entities", {})
        last_response = last_response or conversation_context.get("last_response")
        
        # Construir descripción del contexto
        context_parts = []
        
        if global_entities.get("years"):
            years = list(global_entities["years"])
            if len(years) == 1:
                context_parts.append(f"Años: {years[0]}")
            else:
                context_parts.append(f"Años: {min(years)}-{max(years)}")
        
        if global_entities.get("programs"):
            programs = list(global_entities["programs"])[:3]
            context_parts.append(f"Programas: {', '.join(programs)}")
        
        if global_entities.get("content_types"):
            types = list(global_entities["content_types"])
            context_parts.append(f"Tipos: {', '.join(types)}")
        
        if global_entities.get("topics"):
            topics = list(global_entities["topics"])[:2]
            context_parts.append(f"Temas: {', '.join(topics)}")
        
        if global_entities.get("channels"):
            channels = list(global_entities["channels"])[:2]
            context_parts.append(f"Canales: {', '.join(channels)}")
        
        context_description = "\n".join(context_parts) if context_parts else "Sin contexto específico"
        
        # Construir prompt
        prompt = f"""Mejora la siguiente pregunta del usuario añadiendo contexto implícito de la conversación anterior.

Contexto de la conversación:
{context_description}

{f"Pregunta anterior: {last_query}" if last_query else ""}
    {f"Respuesta anterior (resumen): {last_response[:1000]}" if last_response else ""}

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
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "temperature": 0.2,
                },
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                response_text = result.get("response", "").strip()
                
                # Extraer JSON
                response_text = self._extract_json(response_text)
                
                try:
                    enhancement = json.loads(response_text)
                    
                    if "enhanced_query" not in enhancement:
                        raise ValueError("JSON sin enhanced_query")
                    
                    return enhancement
                    
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"⚠️ Error parseando enhancement: {e}")
                    print(f"Respuesta: {response_text[:200]}")
                    # Fallback: retornar query original
                    return {
                        "enhanced_query": current_query,
                        "confidence": 0.0,
                        "changes_made": []
                    }
            else:
                print(f"⚠️ Error en enhancement request: {response.status_code}")
                return {
                    "enhanced_query": current_query,
                    "confidence": 0.0,
                    "changes_made": []
                }
                
        except Exception as e:
            print(f"⚠️ Error en enhancement LLM: {str(e)}")
            return {
                "enhanced_query": current_query,
                "confidence": 0.0,
                "changes_made": []
            }
    
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
    
    def _fallback_analysis(self, current_query: str, last_query: str) -> Dict[str, Any]:
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
