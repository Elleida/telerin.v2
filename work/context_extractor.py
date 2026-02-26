"""
Extractor de Contexto Conversacional

Este módulo extrae entidades y contexto estructurado de conversaciones:
- Años y fechas
- Canales de TV/Radio
- Programas y personas
- Productos y anunciantes
- Temas y tipos de contenido
"""

from typing import Dict, List, Set, Any, Optional
import re
from datetime import datetime


class ConversationContextExtractor:
    """Extrae contexto estructurado del histórico de conversación"""
    
    # Patrones de extracción de entidades
    YEAR_PATTERNS = [
        r'\b(19[5-6][0-9])\b',  # Años 1950-1969
        r'\baño\s+(19[5-6][0-9])\b',
        r'\ben\s+(19[5-6][0-9])\b',
    ]
    
    # Canales conocidos de TV y Radio
    KNOWN_CHANNELS = {
        "tve", "tve-1", "televisión española", "television española",
        "radio nacional", "radio intercontinental", "radio madrid",
        "ser", "cadena ser", "cope", "onda media", "onda corta"
    }
    
    # Tipos de contenido
    CONTENT_TYPES = {
        "televisión": ["televisión", "television", "tv", "tve", "programa tv", "programas tv"],
        "radio": ["radio", "programa radio", "programas radio", "emisora"],
        "publicidad": ["publicidad", "anuncio", "anuncios", "publicidades", "anunciante"],
        "editorial": ["artículo", "articulo", "reportaje", "editorial", "noticia"],
    }
    
    # Temas comunes
    COMMON_TOPICS = {
        "deportes": ["deportes", "fútbol", "futbol", "baloncesto", "tenis", "ciclismo"],
        "música": ["música", "musica", "canción", "cancion", "concierto"],
        "cine": ["cine", "película", "pelicula", "film", "filmografía"],
        "informativos": ["noticias", "informativo", "telediario", "actualidad"],
        "entretenimiento": ["show", "variedades", "concurso", "entretenimiento"],
        "eurovisión": ["eurovisión", "eurovision", "festival"],
    }
    
    def __init__(self):
        """Inicializa el extractor"""
        pass
    
    def extract_entities_from_text(self, text: str) -> Dict[str, Any]:
        """
        Extrae entidades de un texto dado
        
        Args:
            text: Texto a analizar
            
        Returns:
            Diccionario con entidades encontradas
        """
        text_lower = text.lower()
        
        entities = {
            "years": self._extract_years(text),
            "channels": self._extract_channels(text_lower),
            "content_types": self._extract_content_types(text_lower),
            "topics": self._extract_topics(text_lower),
            "products": self._extract_products(text_lower),
            "programs": self._extract_programs(text),
        }
        
        return entities
    
    def _extract_years(self, text: str) -> Set[int]:
        """Extrae años mencionados en el texto"""
        years = set()
        
        for pattern in self.YEAR_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    year = int(match)
                    if 1950 <= year <= 1969:  # Rango válido para BD TeleRadio
                        years.add(year)
                except ValueError:
                    continue
        
        # Detectar rangos (ej: "1960-1965", "entre 1960 y 1965")
        range_patterns = [
            r'(19[5-6][0-9])\s*[-–—]\s*(19[5-6][0-9])',
            r'entre\s+(19[5-6][0-9])\s+y\s+(19[5-6][0-9])',
            r'desde\s+(19[5-6][0-9])\s+hasta\s+(19[5-6][0-9])',
        ]
        
        for pattern in range_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    start_year = int(match[0])
                    end_year = int(match[1])
                    # Agregar todos los años en el rango
                    for year in range(start_year, end_year + 1):
                        if 1950 <= year <= 1969:
                            years.add(year)
                except (ValueError, IndexError):
                    continue
        
        # Detectar décadas (ej: "años 60", "década de los 60")
        decade_patterns = [
            r'años?\s+([5-6]0)',
            r'década\s+(?:de\s+)?(?:los\s+)?([5-6]0)',
        ]
        
        for pattern in decade_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    decade = int(match)
                    base_year = 1900 + decade
                    # Agregar todos los años de la década
                    for year in range(base_year, base_year + 10):
                        if 1950 <= year <= 1969:
                            years.add(year)
                except ValueError:
                    continue
        
        return years
    
    def _extract_channels(self, text_lower: str) -> Set[str]:
        """Extrae canales de TV/Radio mencionados"""
        channels = set()
        
        for channel in self.KNOWN_CHANNELS:
            if channel in text_lower:
                channels.add(channel.title())
        
        return channels
    
    def _extract_content_types(self, text_lower: str) -> Set[str]:
        """Extrae tipos de contenido mencionados"""
        content_types = set()
        
        for content_type, keywords in self.CONTENT_TYPES.items():
            for keyword in keywords:
                if keyword in text_lower:
                    content_types.add(content_type)
                    break
        
        return content_types
    
    def _extract_topics(self, text_lower: str) -> Set[str]:
        """Extrae temas mencionados"""
        topics = set()
        
        for topic, keywords in self.COMMON_TOPICS.items():
            for keyword in keywords:
                if keyword in text_lower:
                    topics.add(topic)
                    break
        
        return topics
    
    def _extract_products(self, text_lower: str) -> Set[str]:
        """Extrae productos/marcas mencionados (básico)"""
        products = set()
        
        # Marcas comunes en la época
        known_brands = [
            "coca-cola", "cocacola", "pepsi",
            "seat", "renault", "simca",
            "iberia", "aviaco",
            "telefunken", "philips",
            "codorniu", "codorníu",
            "nestlé", "nestle",
        ]
        
        for brand in known_brands:
            if brand in text_lower:
                products.add(brand.title())
        
        return products
    
    def _extract_programs(self, text: str) -> Set[str]:
        """Extrae nombres de programas mencionados (detecta mayúsculas y comillas)"""
        programs = set()
        
        # Primero buscar nombres entre comillas (prioridad 1)
        # Ej: "Caras Nuevas", 'Bonanza', "El Show de Rafa"
        quoted_names = re.findall(r'["\']([^"\']+)["\']', text)
        for name in quoted_names:
            # Solo añadir si tiene al menos 3 caracteres y no es solo números
            if len(name) > 2 and not name.isdigit():
                programs.add(name)
        
        # Buscar palabras capitalizadas que puedan ser nombres de programas
        # Esto es básico, se puede mejorar con NER si es necesario
        capitalized_words = re.findall(r'\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*\b', text)
        
        # Filtrar nombres comunes que no son programas
        common_words = {"Televisión", "Radio", "España", "Madrid", "Barcelona", "Española", "Nacional", "Programa", "Programas"}
        
        for word in capitalized_words:
            if word not in common_words and len(word) > 3:
                programs.add(word)
        
        return programs
    
    def extract_entities_from_results(self, search_results: List[Dict]) -> Dict[str, Any]:
        """
        Extrae entidades de los resultados de búsqueda
        
        Args:
            search_results: Lista de resultados de búsqueda
            
        Returns:
            Diccionario con entidades encontradas
        """
        combined_entities = {
            "years": set(),
            "channels": set(),
            "content_types": set(),
            "topics": set(),
            "products": set(),
            "programs": set(),
        }
        
        for result in search_results:
            # Extraer de campos relevantes (filtrar None)
            text_fields = []
            
            if "title" in result and result["title"]:
                text_fields.append(str(result["title"]))
            if "content_description" in result and result["content_description"]:
                text_fields.append(str(result["content_description"]))
            if "main_title" in result and result["main_title"]:
                text_fields.append(str(result["main_title"]))
            if "channel" in result and result["channel"]:
                text_fields.append(str(result["channel"]))
            if "station" in result and result["station"]:
                text_fields.append(str(result["station"]))
            
            if not text_fields:
                continue
            
            combined_text = " ".join(text_fields)
            entities = self.extract_entities_from_text(combined_text)
            
            # Combinar entidades
            for key in combined_entities:
                combined_entities[key].update(entities.get(key, set()))
        
        return combined_entities
    
    def detect_follow_up_pattern(self, user_query: str, last_query: Optional[str] = None) -> str:
        """
        Detecta el tipo de follow-up pattern
        
        Args:
            user_query: Query actual del usuario
            last_query: Query anterior (opcional)
            
        Returns:
            Tipo de pattern: "narrowing", "expanding", "switching", "clarifying", "none"
        """
        query_lower = user_query.lower().strip()
        
        # Patrones de refinamiento (narrowing)
        narrowing_patterns = [
            r'^¿?solo\s+',
            r'^¿?únicamente\s+',
            r'^¿?solamente\s+',
            r'pero\s+(solo|únicamente|solamente)',
        ]
        
        for pattern in narrowing_patterns:
            if re.search(pattern, query_lower):
                return "narrowing"
        
        # Patrones de expansión (expanding)
        expanding_patterns = [
            r'^¿?y\s+(en|de|sobre|para)',
            r'^¿?también\s+',
            r'^¿?además\s+',
        ]
        
        for pattern in expanding_patterns:
            if re.search(pattern, query_lower):
                return "expanding"
        
        # Patrones de aclaración (clarifying)
        clarifying_patterns = [
            r'^¿?cuánt[oa]s?\s+',
            r'^¿?quién\s+',
            r'^¿?qué\s+años?\s+',
            r'^¿?en\s+qué\s+',
        ]
        
        for pattern in clarifying_patterns:
            if re.search(pattern, query_lower):
                return "clarifying"
        
        # Si no coincide con ningún patrón, es "none" (nueva búsqueda)
        return "none"
    
    def get_relevant_context(
        self,
        conversation_turns: List[Dict],
        last_n_turns: int = 3
    ) -> Dict[str, Any]:
        """
        Obtiene contexto relevante de los últimos N turnos
        
        Args:
            conversation_turns: Lista de turnos de conversación
            last_n_turns: Número de turnos a considerar
            
        Returns:
            Diccionario con contexto relevante
        """
        recent_turns = conversation_turns[-last_n_turns:] if conversation_turns else []
        
        if not recent_turns:
            return {
                "has_context": False,
                "entities": {},
                "last_intent": None,
            }
        
        # Combinar entidades de turnos recientes
        combined_entities = {
            "years": set(),
            "channels": set(),
            "content_types": set(),
            "topics": set(),
            "products": set(),
            "programs": set(),
        }
        
        for turn in recent_turns:
            entities = turn.get("entities_found", {})
            for key in combined_entities:
                if key in entities:
                    combined_entities[key].update(entities[key])
        
        # Convertir sets a listas
        for key in combined_entities:
            combined_entities[key] = list(combined_entities[key])
        
        return {
            "has_context": True,
            "entities": combined_entities,
            "last_intent": recent_turns[-1].get("query_type", "unknown"),
            "last_query": recent_turns[-1].get("user_query", ""),
            "turn_count": len(recent_turns),
        }


# Función de utilidad para extraer entidades rápidamente
def extract_entities(text: str) -> Dict[str, Any]:
    """
    Función helper para extraer entidades de un texto
    
    Args:
        text: Texto a analizar
        
    Returns:
        Diccionario con entidades encontradas
    """
    extractor = ConversationContextExtractor()
    return extractor.extract_entities_from_text(text)
