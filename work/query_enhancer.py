"""
Query Enhancer - Mejora de Queries con Contexto

Este módulo mejora las queries del usuario usando el contexto conversacional:
- Detecta referencias implícitas ("Y en televisión?")
- Añade contexto de años, canales, temas previos
- Resuelve ambigüedades usando histórico
- Mantiene coherencia con conversación anterior
"""

from typing import Dict, Any, Optional, TYPE_CHECKING
import re
from context_extractor import ConversationContextExtractor

if TYPE_CHECKING:
    from memory import ConversationTurn, ConversationMemory


class QueryEnhancer:
    """Mejora queries con contexto conversacional previo"""
    
    def __init__(self, conversation_memory):
        """
        Inicializa el enhancer con memoria conversacional
        
        Args:
            conversation_memory: Instancia de ConversationMemory
        """
        self.memory = conversation_memory
        self.extractor = ConversationContextExtractor()
    
    def enhance_query(self, user_query: str) -> Optional[str]:
        """
        Mejora una query con contexto previo usando LLM
        
        Args:
            user_query: Query original del usuario
            
        Returns:
            Query mejorada con contexto, o None si no necesita mejora
        """
        # Si no hay contexto previo, no mejorar
        if not self.memory.messages:
            return None
        
        # Detectar si es un follow-up
        if not self.memory.is_contextual_follow_up(user_query):
            # No parece ser un follow-up, retornar query original
            return None
        
        # Obtener contexto reciente
        context = self.memory.extract_context()
        last_turn = self.memory.get_last_turn()
        
        if not last_turn:
            return None
        
        # Usar LLM para mejorar la query con contexto
        try:
            from llm_context_analyzer import get_llm_analyzer
            
            analyzer = get_llm_analyzer()
            last_query = last_turn.get("user_query", "")
            
            # Preparar contexto conversacional
            last_response = last_turn.get("response", "")
            conversation_context = {
                "context_summary": context.get("context_summary", {}),
                "global_entities": context.get("global_entities", {}),
                "last_query": last_query,
                "last_response": last_response,
            }
            
            enhancement = analyzer.enhance_query_with_context(
                current_query=user_query,
                conversation_context=conversation_context,
                last_query=last_query,
                last_response=last_response
            )
            
            enhanced_query = enhancement.get("enhanced_query", user_query)
            confidence = enhancement.get("confidence", 0.0)
            
            print(f"🤖 Query mejorada (confianza: {confidence:.2f}): {enhanced_query}")
            
            # Solo usar la query mejorada si hay confianza razonable
            if confidence > 0.5 and enhanced_query != user_query:
                return enhanced_query
            else:
                # Fallback a método basado en reglas
                return self._enhance_with_rules(user_query, context, last_turn)
                
        except Exception as e:
            print(f"⚠️ Error en enhancement LLM, usando fallback: {e}")
            # Fallback a método basado en reglas
            return self._enhance_with_rules(user_query, context, last_turn)
    
    def _enhance_with_rules(self, user_query: str, context: Dict, last_turn: "ConversationTurn") -> Optional[str]:
        """
        Método de fallback usando reglas para mejorar queries
        """
        print("   [Fallback] Usando reglas simples para mejorar query")
        
        query_lower = user_query.lower().strip()
        last_query = last_turn.get("user_query", "")
        
        # Caso 1: Pregunta sobre características sin mencionar el sujeto
        # Ej: "¿Qué tipo de programa era?", "¿Quién lo presentaba?"
        characteristic_keywords = ["qué tipo", "cómo era", "quién", "dónde", "cuándo", "qué era"]
        
        if any(keyword in query_lower for keyword in characteristic_keywords):
            # Extraer nombres propios de la pregunta anterior con comillas
            import re
            
            # Buscar nombres entre comillas primero
            quoted_names = re.findall(r'["\']([^"\'\.\?]+)["\']', last_query)
            if quoted_names:
                subject = quoted_names[0].strip()
                return f"{user_query} {subject}"
            
            # Buscar nombres propios capitalizados (2+ palabras seguidas capitalizadas)
            capitalized_phrases = re.findall(r'\b([A-Z][a-zá-úñ]+(?:\s+[A-Z][a-zá-úñ]+)+)\b', last_query)
            if capitalized_phrases:
                subject = capitalized_phrases[0]
                return f"{user_query} {subject}"
            
            # Si hay entidades de programas en el contexto, usar la primera
            entities = last_turn.get("entities_found", {})
            if entities and entities.get("programs"):
                programs = list(entities["programs"])
                # Filtrar programas "falsos" como días de la semana
                days = {"lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"}
                real_programs = [p for p in programs if p.lower() not in days and len(p.split()) >= 2]
                if real_programs:
                    return f"{user_query} {real_programs[0]}"
        
        # Caso 2: "Y en..." - añadir tema de la pregunta anterior
        if query_lower.startswith(("¿y en", "y en", "¿también en", "también en")):
            # Extraer el tema principal de la pregunta anterior
            import re
            # Buscar palabras clave como "programas", "emisiones", "anuncios"
            keywords = re.findall(r'\b(programas?|emisiones?|anuncios?|publicidad)\b', last_query.lower())
            if keywords:
                topic = keywords[0]
                # Buscar años
                years = re.findall(r'\b(19\d{2})\b', last_query)
                if years:
                    return f"{user_query} de {topic} en {years[0]}"
                return f"{user_query} de {topic}"
        
        # Fallback: retornar query original
        print("   [Fallback] No se pudo mejorar con reglas simples")
        return user_query
