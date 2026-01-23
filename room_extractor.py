#!/usr/bin/env python3
"""
FireAI Pro - Room Polygon Extractor v1.0
=========================================
Step 1.3: Room Polygon Extraction

Uses wall segments to form closed room polygons:
- Finds wall intersections
- Traces room boundaries
- Calculates room areas
- Associates text labels with rooms

VERSION: 1.0.0
"""

import math
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Set
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FireAI.RoomExtractor")


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class Point2D:
    """2D Point"""
    x: float
    y: float
    
    def distance_to(self, other: 'Point2D') -> float:
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)
    
    def __hash__(self):
        return hash((round(self.x, 1), round(self.y, 1)))
    
    def __eq__(self, other):
        if not isinstance(other, Point2D):
            return False
        return abs(self.x - other.x) < 0.5 and abs(self.y - other.y) < 0.5


@dataclass
class WallSegment:
    """Wall segment for room detection"""
    start: Point2D
    end: Point2D
    
    @property
    def length(self) -> float:
        return self.start.distance_to(self.end)
    
    @property
    def is_horizontal(self) -> bool:
        return abs(self.end.y - self.start.y) < 1.0
    
    @property
    def is_vertical(self) -> bool:
        return abs(self.end.x - self.start.x) < 1.0


@dataclass
class Room:
    """Detected room polygon"""
    id: str
    points: List[Point2D]
    area_sqft: float = 0
    perimeter_ft: float = 0
    label: str = ""
    room_type: str = "unknown"
    centroid: Point2D = None
    
    # Bounding box
    min_x: float = 0
    max_x: float = 0
    min_y: float = 0
    max_y: float = 0
    
    def __post_init__(self):
        if self.points:
            self._calculate_properties()
    
    def _calculate_properties(self):
        """Calculate area, centroid, bounding box"""
        if len(self.points) < 3:
            return
        
        # Bounding box
        xs = [p.x for p in self.points]
        ys = [p.y for p in self.points]
        self.min_x = min(xs)
        self.max_x = max(xs)
        self.min_y = min(ys)
        self.max_y = max(ys)
        
        # Centroid
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        self.centroid = Point2D(cx, cy)
        
        # Area using shoelace formula
        n = len(self.points)
        area = 0
        for i in range(n):
            j = (i + 1) % n
            area += self.points[i].x * self.points[j].y
            area -= self.points[j].x * self.points[i].y
        self.area_sqft = abs(area) / 2
        
        # Perimeter
        perimeter = 0
        for i in range(n):
            j = (i + 1) % n
            perimeter += self.points[i].distance_to(self.points[j])
        self.perimeter_ft = perimeter
    
    def contains_point(self, p: Point2D) -> bool:
        """Check if point is inside room polygon"""
        if not self.points or len(self.points) < 3:
            return False
        
        # Ray casting algorithm
        n = len(self.points)
        inside = False
        
        j = n - 1
        for i in range(n):
            xi, yi = self.points[i].x, self.points[i].y
            xj, yj = self.points[j].x, self.points[j].y
            
            if ((yi > p.y) != (yj > p.y)) and \
               (p.x < (xj - xi) * (p.y - yi) / (yj - yi) + xi):
                inside = not inside
            
            j = i
        
        return inside
    
    @property
    def width(self) -> float:
        return self.max_x - self.min_x
    
    @property
    def height(self) -> float:
        return self.max_y - self.min_y


@dataclass
class ExtractedRooms:
    """Complete room extraction result"""
    rooms: List[Room] = field(default_factory=list)
    total_area_sqft: float = 0
    room_count: int = 0
    labeled_count: int = 0
    scale_factor: float = 1.0
    confidence: float = 0


# =============================================================================
# ROOM POLYGON EXTRACTOR
# =============================================================================

class RoomPolygonExtractor:
    """
    Extract room polygons from wall segments.
    """
    
    def __init__(self, 
                 min_room_area: float = 50,      # Minimum room area in sqft
                 max_room_area: float = 50000,   # Maximum room area in sqft
                 merge_tolerance: float = 5.0):  # Tolerance for point matching
        
        self.min_room_area = min_room_area
        self.max_room_area = max_room_area
        self.merge_tolerance = merge_tolerance
    
    def extract_rooms_from_walls(self, 
                                  walls: List[Tuple],
                                  texts: List = None,
                                  scale_factor: float = 96.0) -> ExtractedRooms:
        """
        Extract room polygons from wall segments.
        
        Args:
            walls: List of wall tuples (x1, y1, x2, y2)
            texts: List of text elements for room labeling
            scale_factor: Points per foot (for area calculation)
        
        Returns:
            ExtractedRooms object
        """
        result = ExtractedRooms(scale_factor=scale_factor)
        
        logger.info(f"🏠 Room extraction starting with {len(walls)} walls")
        
        # Convert walls to segments
        segments = []
        for w in walls:
            segments.append(WallSegment(
                start=Point2D(w[0], w[1]),
                end=Point2D(w[2], w[3])
            ))
        
        # Find all wall intersections
        intersections = self._find_intersections(segments)
        logger.info(f"   Found {len(intersections)} intersection points")
        
        # Build graph of wall connectivity
        graph = self._build_wall_graph(segments, intersections)
        logger.info(f"   Built graph with {len(graph)} nodes")
        
        # Find closed loops (rooms)
        rooms = self._find_rooms(graph)
        logger.info(f"   Found {len(rooms)} potential rooms")
        
        # Filter by area and create Room objects
        room_id = 1
        for room_points in rooms:
            if len(room_points) < 3:
                continue
            
            room = Room(
                id=f"ROOM-{room_id:03d}",
                points=room_points
            )
            
            # Convert area from points^2 to sqft
            area_pts = room.area_sqft
            area_sqft = area_pts / (scale_factor ** 2)
            room.area_sqft = area_sqft
            room.perimeter_ft = room.perimeter_ft / scale_factor
            
            # Filter by area
            if self.min_room_area <= area_sqft <= self.max_room_area:
                result.rooms.append(room)
                room_id += 1
        
        # Assign labels to rooms
        if texts:
            self._assign_labels(result.rooms, texts, scale_factor)
        
        # Calculate totals
        result.total_area_sqft = sum(r.area_sqft for r in result.rooms)
        result.room_count = len(result.rooms)
        result.labeled_count = sum(1 for r in result.rooms if r.label)
        result.confidence = self._calculate_confidence(result)
        
        logger.info(f"✅ Room extraction complete:")
        logger.info(f"   Rooms: {result.room_count}")
        logger.info(f"   Total Area: {result.total_area_sqft:,.0f} sqft")
        logger.info(f"   Labeled: {result.labeled_count}")
        
        return result
    
    def _find_intersections(self, segments: List[WallSegment]) -> Set[Point2D]:
        """Find all points where walls intersect"""
        intersections = set()
        
        for i, seg1 in enumerate(segments):
            # Add endpoints
            intersections.add(seg1.start)
            intersections.add(seg1.end)
            
            # Find intersections with other segments
            for j, seg2 in enumerate(segments[i+1:], i+1):
                intersection = self._line_intersection(seg1, seg2)
                if intersection:
                    intersections.add(intersection)
        
        return intersections
    
    def _line_intersection(self, seg1: WallSegment, seg2: WallSegment) -> Optional[Point2D]:
        """Find intersection point of two line segments"""
        x1, y1 = seg1.start.x, seg1.start.y
        x2, y2 = seg1.end.x, seg1.end.y
        x3, y3 = seg2.start.x, seg2.start.y
        x4, y4 = seg2.end.x, seg2.end.y
        
        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denom) < 0.001:
            return None  # Parallel lines
        
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
        u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom
        
        # Check if intersection is within both segments
        if 0 <= t <= 1 and 0 <= u <= 1:
            x = x1 + t * (x2 - x1)
            y = y1 + t * (y2 - y1)
            return Point2D(x, y)
        
        return None
    
    def _build_wall_graph(self, segments: List[WallSegment], 
                          intersections: Set[Point2D]) -> Dict[Point2D, List[Point2D]]:
        """Build adjacency graph of wall connectivity"""
        graph = defaultdict(list)
        
        # For each segment, find which intersection points it contains
        for seg in segments:
            # Get points on this segment
            segment_points = []
            
            for point in intersections:
                if self._point_on_segment(point, seg):
                    segment_points.append(point)
            
            # Sort points along segment
            if seg.is_horizontal:
                segment_points.sort(key=lambda p: p.x)
            else:
                segment_points.sort(key=lambda p: p.y)
            
            # Connect adjacent points
            for i in range(len(segment_points) - 1):
                p1, p2 = segment_points[i], segment_points[i+1]
                if p1 != p2:
                    graph[p1].append(p2)
                    graph[p2].append(p1)
        
        return dict(graph)
    
    def _point_on_segment(self, point: Point2D, seg: WallSegment) -> bool:
        """Check if point lies on segment"""
        # Check if point is between segment endpoints
        min_x = min(seg.start.x, seg.end.x) - self.merge_tolerance
        max_x = max(seg.start.x, seg.end.x) + self.merge_tolerance
        min_y = min(seg.start.y, seg.end.y) - self.merge_tolerance
        max_y = max(seg.start.y, seg.end.y) + self.merge_tolerance
        
        if not (min_x <= point.x <= max_x and min_y <= point.y <= max_y):
            return False
        
        # Check if point is on line
        if seg.is_horizontal:
            return abs(point.y - seg.start.y) < self.merge_tolerance
        elif seg.is_vertical:
            return abs(point.x - seg.start.x) < self.merge_tolerance
        else:
            # General case - check distance to line
            d = self._point_line_distance(point, seg)
            return d < self.merge_tolerance
    
    def _point_line_distance(self, point: Point2D, seg: WallSegment) -> float:
        """Calculate distance from point to line segment"""
        x0, y0 = point.x, point.y
        x1, y1 = seg.start.x, seg.start.y
        x2, y2 = seg.end.x, seg.end.y
        
        # Line length squared
        l2 = (x2 - x1)**2 + (y2 - y1)**2
        if l2 == 0:
            return point.distance_to(seg.start)
        
        # Project point onto line
        t = max(0, min(1, ((x0 - x1) * (x2 - x1) + (y0 - y1) * (y2 - y1)) / l2))
        
        proj_x = x1 + t * (x2 - x1)
        proj_y = y1 + t * (y2 - y1)
        
        return math.sqrt((x0 - proj_x)**2 + (y0 - proj_y)**2)
    
    def _find_rooms(self, graph: Dict[Point2D, List[Point2D]]) -> List[List[Point2D]]:
        """Find all closed loops (rooms) in the wall graph"""
        rooms = []
        visited_edges = set()
        
        # Simple room finding: look for small cycles
        for start_node in graph:
            for next_node in graph.get(start_node, []):
                edge = (start_node, next_node)
                if edge in visited_edges or (next_node, start_node) in visited_edges:
                    continue
                
                # Try to find a cycle starting from this edge
                path = self._find_cycle(graph, start_node, next_node, max_length=20)
                if path and len(path) >= 3:
                    rooms.append(path)
                    
                    # Mark edges as visited
                    for i in range(len(path)):
                        p1 = path[i]
                        p2 = path[(i + 1) % len(path)]
                        visited_edges.add((p1, p2))
                        visited_edges.add((p2, p1))
        
        return rooms
    
    def _find_cycle(self, graph: Dict, start: Point2D, second: Point2D, 
                    max_length: int = 20) -> Optional[List[Point2D]]:
        """Find a cycle in the graph starting from an edge"""
        # Use right-hand rule to trace the room boundary
        path = [start, second]
        current = second
        prev = start
        
        for _ in range(max_length):
            neighbors = graph.get(current, [])
            if not neighbors:
                return None
            
            # Find next node using right-hand rule
            next_node = self._get_rightmost_turn(prev, current, neighbors)
            
            if next_node is None:
                return None
            
            if next_node == start and len(path) >= 3:
                return path  # Found a cycle
            
            if next_node in path[1:]:
                return None  # Self-intersection
            
            path.append(next_node)
            prev = current
            current = next_node
        
        return None  # Max length exceeded
    
    def _get_rightmost_turn(self, prev: Point2D, current: Point2D, 
                           neighbors: List[Point2D]) -> Optional[Point2D]:
        """Get the neighbor that represents the rightmost turn"""
        if not neighbors:
            return None
        
        # Direction we came from
        incoming_angle = math.atan2(current.y - prev.y, current.x - prev.x)
        
        best_neighbor = None
        best_angle = float('inf')
        
        for neighbor in neighbors:
            if neighbor == prev:
                continue
            
            # Direction to neighbor
            outgoing_angle = math.atan2(neighbor.y - current.y, neighbor.x - current.x)
            
            # Calculate turn angle (right turn is negative)
            turn_angle = outgoing_angle - incoming_angle
            
            # Normalize to [-pi, pi]
            while turn_angle > math.pi:
                turn_angle -= 2 * math.pi
            while turn_angle < -math.pi:
                turn_angle += 2 * math.pi
            
            # We want the smallest right turn (or largest left turn if no right turns)
            if turn_angle < best_angle:
                best_angle = turn_angle
                best_neighbor = neighbor
        
        return best_neighbor
    
    def _assign_labels(self, rooms: List[Room], texts: List, scale_factor: float):
        """Assign text labels to rooms based on position"""
        # Room type keywords
        room_types = {
            'office': 'office',
            'conf': 'conference',
            'meeting': 'conference',
            'lobby': 'lobby',
            'reception': 'lobby',
            'corridor': 'corridor',
            'hall': 'corridor',
            'restroom': 'restroom',
            'bathroom': 'restroom',
            'kitchen': 'kitchen',
            'break': 'break_room',
            'storage': 'storage',
            'closet': 'storage',
            'mechanical': 'mechanical',
            'electrical': 'electrical',
            'elec': 'electrical',
            'mech': 'mechanical',
            'stair': 'stair',
            'elevator': 'elevator',
            'elev': 'elevator',
            'server': 'server_room',
            'data': 'server_room',
            'copy': 'copy_room',
            'mail': 'mail_room',
        }
        
        for text in texts:
            text_point = Point2D(text.x, text.y)
            
            # Find which room contains this text
            for room in rooms:
                # Scale room coordinates for comparison
                if room.contains_point(text_point):
                    if not room.label:  # Don't overwrite
                        room.label = text.text
                        
                        # Determine room type
                        text_lower = text.text.lower()
                        for keyword, rtype in room_types.items():
                            if keyword in text_lower:
                                room.room_type = rtype
                                break
                    break
    
    def _calculate_confidence(self, result: ExtractedRooms) -> float:
        """Calculate confidence score"""
        score = 0
        
        # Has rooms
        if result.room_count > 0:
            score += 30
        
        # Reasonable number of rooms
        if 5 <= result.room_count <= 100:
            score += 20
        
        # Has labeled rooms
        if result.labeled_count > 0:
            score += 20
        
        # Total area is reasonable
        if 500 <= result.total_area_sqft <= 100000:
            score += 15
        
        # Rooms have reasonable areas
        areas = [r.area_sqft for r in result.rooms]
        if areas and min(areas) >= 50 and max(areas) <= 10000:
            score += 15
        
        return min(100, score)


# =============================================================================
# GRID-BASED ROOM DETECTION (Alternate Method)
# =============================================================================

class GridBasedRoomDetector:
    """
    Detect rooms using a grid-based flood fill approach.
    More robust for complex floor plans.
    """
    
    def __init__(self, grid_resolution: float = 5.0):
        """
        Args:
            grid_resolution: Size of each grid cell in points
        """
        self.resolution = grid_resolution
    
    def detect_rooms(self, walls: List[Tuple], bounds: Tuple,
                     texts: List = None, scale_factor: float = 96.0) -> ExtractedRooms:
        """
        Detect rooms using grid-based flood fill.
        
        Args:
            walls: List of wall tuples (x1, y1, x2, y2)
            bounds: (min_x, min_y, max_x, max_y)
            texts: List of text elements
            scale_factor: Points per foot
        """
        result = ExtractedRooms(scale_factor=scale_factor)
        
        min_x, min_y, max_x, max_y = bounds
        
        # Create grid
        width = int((max_x - min_x) / self.resolution) + 1
        height = int((max_y - min_y) / self.resolution) + 1
        
        logger.info(f"🔲 Creating {width}x{height} grid")
        
        # Initialize grid (0 = unknown, -1 = wall, >0 = room ID)
        grid = [[0] * width for _ in range(height)]
        
        # Mark walls on grid
        for w in walls:
            self._draw_wall_on_grid(grid, w, min_x, min_y)
        
        # Flood fill to find rooms
        room_id = 1
        rooms_pixels = {}  # room_id -> list of (x, y) grid coords
        
        for y in range(height):
            for x in range(width):
                if grid[y][x] == 0:  # Unfilled cell
                    pixels = self._flood_fill(grid, x, y, room_id)
                    if pixels and len(pixels) >= 10:  # Minimum size
                        rooms_pixels[room_id] = pixels
                        room_id += 1
        
        logger.info(f"   Found {len(rooms_pixels)} room regions")
        
        # Convert pixel regions to room polygons
        for rid, pixels in rooms_pixels.items():
            room = self._pixels_to_room(pixels, rid, min_x, min_y, scale_factor)
            if room and room.area_sqft >= 50:  # Minimum area
                result.rooms.append(room)
        
        # Assign labels
        if texts:
            self._assign_labels(result.rooms, texts)
        
        result.room_count = len(result.rooms)
        result.total_area_sqft = sum(r.area_sqft for r in result.rooms)
        result.labeled_count = sum(1 for r in result.rooms if r.label)
        result.confidence = 70 if result.room_count > 0 else 30
        
        logger.info(f"✅ Grid detection complete: {result.room_count} rooms, {result.total_area_sqft:,.0f} sqft")
        
        return result
    
    def _draw_wall_on_grid(self, grid: List[List[int]], wall: Tuple, 
                           min_x: float, min_y: float):
        """Draw a wall line on the grid"""
        x1, y1, x2, y2 = wall
        
        # Convert to grid coordinates
        gx1 = int((x1 - min_x) / self.resolution)
        gy1 = int((y1 - min_y) / self.resolution)
        gx2 = int((x2 - min_x) / self.resolution)
        gy2 = int((y2 - min_y) / self.resolution)
        
        # Bresenham's line algorithm
        dx = abs(gx2 - gx1)
        dy = abs(gy2 - gy1)
        sx = 1 if gx1 < gx2 else -1
        sy = 1 if gy1 < gy2 else -1
        err = dx - dy
        
        height = len(grid)
        width = len(grid[0]) if grid else 0
        
        while True:
            if 0 <= gx1 < width and 0 <= gy1 < height:
                grid[gy1][gx1] = -1  # Mark as wall
            
            if gx1 == gx2 and gy1 == gy2:
                break
            
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                gx1 += sx
            if e2 < dx:
                err += dx
                gy1 += sy
    
    def _flood_fill(self, grid: List[List[int]], start_x: int, start_y: int, 
                    room_id: int) -> List[Tuple[int, int]]:
        """Flood fill from starting point, return list of filled pixels"""
        height = len(grid)
        width = len(grid[0]) if grid else 0
        
        if not (0 <= start_x < width and 0 <= start_y < height):
            return []
        
        if grid[start_y][start_x] != 0:
            return []
        
        pixels = []
        stack = [(start_x, start_y)]
        
        while stack:
            x, y = stack.pop()
            
            if not (0 <= x < width and 0 <= y < height):
                continue
            
            if grid[y][x] != 0:
                continue
            
            grid[y][x] = room_id
            pixels.append((x, y))
            
            # Add neighbors
            stack.extend([(x+1, y), (x-1, y), (x, y+1), (x, y-1)])
        
        return pixels
    
    def _pixels_to_room(self, pixels: List[Tuple[int, int]], room_id: int,
                        min_x: float, min_y: float, scale_factor: float) -> Optional[Room]:
        """Convert pixel region to Room object"""
        if not pixels:
            return None
        
        # Find bounding box
        xs = [p[0] for p in pixels]
        ys = [p[1] for p in pixels]
        
        gmin_x, gmax_x = min(xs), max(xs)
        gmin_y, gmax_y = min(ys), max(ys)
        
        # Convert back to drawing coordinates
        room_min_x = min_x + gmin_x * self.resolution
        room_max_x = min_x + (gmax_x + 1) * self.resolution
        room_min_y = min_y + gmin_y * self.resolution
        room_max_y = min_y + (gmax_y + 1) * self.resolution
        
        # Create simple rectangular room
        points = [
            Point2D(room_min_x, room_min_y),
            Point2D(room_max_x, room_min_y),
            Point2D(room_max_x, room_max_y),
            Point2D(room_min_x, room_max_y),
        ]
        
        # Calculate area from pixels
        area_pts_sq = len(pixels) * (self.resolution ** 2)
        area_sqft = area_pts_sq / (scale_factor ** 2)
        
        room = Room(
            id=f"ROOM-{room_id:03d}",
            points=points,
            area_sqft=area_sqft,
            min_x=room_min_x,
            max_x=room_max_x,
            min_y=room_min_y,
            max_y=room_max_y,
            centroid=Point2D((room_min_x + room_max_x)/2, (room_min_y + room_max_y)/2)
        )
        
        return room
    
    def _assign_labels(self, rooms: List[Room], texts: List):
        """Assign text labels to rooms"""
        for text in texts:
            text_point = Point2D(text.x, text.y)
            
            for room in rooms:
                if (room.min_x <= text_point.x <= room.max_x and
                    room.min_y <= text_point.y <= room.max_y):
                    if not room.label:
                        room.label = text.text
                    break


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def extract_rooms(walls: List[Tuple], texts: List = None, 
                  scale_factor: float = 96.0,
                  bounds: Tuple = None,
                  method: str = 'grid') -> ExtractedRooms:
    """
    Extract rooms from wall segments.
    
    Args:
        walls: List of wall tuples (x1, y1, x2, y2)
        texts: List of text elements
        scale_factor: Points per foot
        bounds: Drawing bounds (min_x, min_y, max_x, max_y)
        method: 'polygon' or 'grid'
    
    Returns:
        ExtractedRooms object
    """
    if method == 'polygon':
        extractor = RoomPolygonExtractor()
        return extractor.extract_rooms_from_walls(walls, texts, scale_factor)
    else:
        if not bounds:
            # Calculate bounds from walls
            all_x = [w[0] for w in walls] + [w[2] for w in walls]
            all_y = [w[1] for w in walls] + [w[3] for w in walls]
            bounds = (min(all_x), min(all_y), max(all_x), max(all_y))
        
        detector = GridBasedRoomDetector(grid_resolution=10.0)
        return detector.detect_rooms(walls, bounds, texts, scale_factor)


def rooms_to_dict(result: ExtractedRooms) -> Dict:
    """Convert ExtractedRooms to JSON-serializable dict"""
    return {
        'room_count': result.room_count,
        'total_area_sqft': result.total_area_sqft,
        'labeled_count': result.labeled_count,
        'confidence': result.confidence,
        'rooms': [
            {
                'id': r.id,
                'label': r.label,
                'room_type': r.room_type,
                'area_sqft': r.area_sqft,
                'centroid': {'x': r.centroid.x, 'y': r.centroid.y} if r.centroid else None,
                'bounds': {
                    'min_x': r.min_x,
                    'min_y': r.min_y,
                    'max_x': r.max_x,
                    'max_y': r.max_y
                }
            }
            for r in result.rooms
        ]
    }


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("🏠 FireAI Pro - Room Polygon Extractor v1.0")
    print("=" * 60)
    print("\nStep 1.3: Room Polygon Extraction")
    print("\nCapabilities:")
    print("  ✅ Find wall intersections")
    print("  ✅ Trace room boundaries")
    print("  ✅ Calculate room areas")
    print("  ✅ Associate text labels with rooms")
    print("  ✅ Grid-based detection (fallback)")
    print("\nUsage:")
    print("  rooms = extract_rooms(walls, texts, scale_factor=96.0)")
