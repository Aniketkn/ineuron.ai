################################################################################
# Module: setup_sp.py
# Description: this module contains functions to set up sample points stats within study regions

################################################################################

import os

import geopandas as gpd
import networkx as nx
import numpy as np
import pandana as pdna
import pandas as pd
from scipy.stats import zscore

import osmnx as ox


def read_proj_graphml(proj_graphml_filepath, ori_graphml_filepath, to_crs):
    """
    Read a projected graph from local disk if exist,
    otherwise, reproject origional graphml to the UTM zone appropriate for its geographic location,
    and save the projected graph to local disk

    Parameters
    ----------
    proj_graphml_filepath: string
        the projected graphml filepath
    ori_graphml_filepath: string
        the original graphml filepath
    to_crs: dict or string or pyproj.CRS
        project to this CRS

    Returns
    -------
    networkx multidigraph
    """
    # if the projected graphml file already exist in disk, then load it from the path
    if os.path.isfile(proj_graphml_filepath):
        print("Read network from disk.")
        return ox.load_graphml(proj_graphml_filepath)

    # else, read original study region graphml and reproject it
    else:
        print("Reproject network, and save the projected network to disk")

        # load and project origional graphml from disk
        G = ox.load_graphml(ori_graphml_filepath)
        G_proj = ox.project_graph(G, to_crs=to_crs)
        # save projected graphml to disk
        ox.save_graphml(G_proj, proj_graphml_filepath)

        return G_proj


def calc_sp_pop_intect_density_multi(G_proj, hexes, distance, rows, node, index):
    """
    Calculate population and intersection density for each sample point

    This function is for multiprocessing. A subnetwork will be created for
    each sample point as a neighborhood and then intersect the hexes with pop
    and intersection data. Population and intersection density for each sample
    point are caculated by averaging the intersected hexes density data

    Parameters
    ----------
    G_proj: networkx multidigraph
    hexes: GeoDataFrame
        hexagon layers containing pop and intersection info
    distance: int
        distance to search around the place geometry, in meters
    rows: int
        the number of rows to loop
    node: list
        the list of osmid of nodes
    index: int
        loop number

    Returns
    -------
    list
    """
    if index % 100 == 0:
        print("{0} / {1}".format(index, rows))

    # create subgraph of neighbors centered at a node within a given radius.
    subgraph_proj = nx.ego_graph(G_proj, node, radius=distance, distance="length")

    # convert subgraph into edge GeoDataFrame
    try:
        subgraph_gdf = ox.graph_to_gdfs(subgraph_proj, nodes=False, edges=True, fill_edge_geometry=True)

        # intersect GeoDataFrame with hexes
        if len(subgraph_gdf) > 0:
            intersections = gpd.sjoin(hexes, subgraph_gdf, how="inner", op="intersects")

            # drop all rows where 'index_right' is nan
            intersections = intersections[intersections["index_right"].notnull()]
            # remove rows where 'index' is duplicate
            intersections = intersections.drop_duplicates(subset=["index"])
            # return list of nodes with osmid, pop and intersection density
            return [
                node,
                float(intersections["pop_per_sqkm"].mean()),
                float(intersections["intersections_per_sqkm"].mean()),
            ]
        else:
            return [node]

    except ValueError as e:
        return [node]


def calc_sp_pop_intect_density(osmid, G_proj, hexes, field_pop, field_intersection, distance, counter, rows):
    """
    Calculate population and intersection density for a sample point

    This function is apply the single thread method. A subnetwork will be
    created for each sample point as a neighborhood and then intersect the
    hexes with pop and intersection data. Population and intersection density
    for each sample point are caculated by averaging the intersected hexes
    density data


    Parameters
    ----------
    osmid: int
        the id of a node
    G_proj: networkx multidigraph
    hexes: GeoDataFrame
        hexagon layers containing pop and intersection info
    field_pop: str
        the field name of pop density
    field_intersection: str
        the field name of intersection density
    distance: int
        distance to search around the place geometry, in meters
    counter: value
        counter for process times(Object from multiprocessing)
    rows: int
        the number of rows to loop

    Returns
    -------
    Series
    """
    # apply calc_sp_pop_intect_density_single function to get population and intersection density for sample point
    pop_per_sqkm, int_per_sqkm = calc_sp_pop_intect_density_single(osmid, G_proj, hexes, distance, counter, rows)
    return pd.Series({field_pop: pop_per_sqkm, field_intersection: int_per_sqkm})


def calc_sp_pop_intect_density_single(osmid, G_proj, hexes, distance, counter, rows):
    """
    Calculate population and intersection density for a sample point

    This function is for single thread. A subnetwork will be created for each
    sample point as a neighborhood and then intersect the hexes with pop and
    intersection data. Population and intersection density for each sample
    point are caculated by averaging the intersected hexes density data

    Parameters
    ----------
    osmid: int
        the id of a node
    G_proj: networkx multidigraph
    hexes: GeoDataFrame
        hexagon layers containing pop and intersection info
    distance: int
        distance to search around the place geometry, in meters
    counter: value
        counter for process times (object from multiprocessing)
    rows: int
        the number of rows to loop

    Returns
    -------
    tuple, (pop density, intersection density)
    """
    with counter.get_lock():
        # print(counter.value)
        counter.value += 1
        if counter.value % 100 == 0:
            print("{0} / {1}".format(counter.value, rows))
    orig_node = osmid
    # create subgraph of neighbors centered at a node within a given radius.
    subgraph_proj = nx.ego_graph(G_proj, orig_node, radius=distance, distance="length")
    if len(subgraph_proj.edges) > 0:
        # convert subgraph into edge GeoDataFrame
        subgraph_gdf = ox.graph_to_gdfs(subgraph_proj, nodes=False, edges=True, fill_edge_geometry=True)
        intersections = gpd.sjoin(hexes, subgraph_gdf, how="inner", op="intersects")
        # drop all rows where 'index_right' is nan
        intersections = intersections[intersections["index_right"].notnull()]
        # remove rows where 'index' is duplicate
        intersections = intersections.drop_duplicates(subset=["index"])
        # return tuple, pop and intersection density for sample point
        return (intersections["pop_per_sqkm"].mean(), intersections["intersections_per_sqkm"].mean())
    else:
        return (np.nan, np.nan)


def createHexid(sp, hex):
    """
    Create hex_id for sample point, if it not exists

    Parameters
    ----------
    sp: GeoDataFrame
        sample point GeoDataFrame
    hex: GeoDataFrame
        hexagon GeoDataFrame

    Returns
    -------
    GeoDataFrame
    """
    if "hex_id" not in sp.columns.tolist():
        # get sample point dataframe columns
        print("Create hex_id for sample points")
        samplePoint_column = sp.columns.tolist()
        samplePoint_column.append("index")

        # join id from hex to each sample point
        samplePointsData = gpd.sjoin(sp, hex, how="left", op="within")
        samplePointsData = samplePointsData[samplePoint_column].copy()
        samplePointsData.rename(columns={"index": "hex_id"}, inplace=True)
        return samplePointsData
    else:
        print("hex_id' already in sample point.")


def create_pdna_net(gdf_nodes, gdf_edges, predistance=500):
    """
    Create pandana network to prepare for calculating the accessibility to destinations
    The network is comprised of a set of nodes and edges.

    Parameters
    ----------
    gdf_nodes: GeoDataFrame
    gdf_edges: GeoDataFrame
    predistance: int
        the distance of search (in meters), default is 500 meters

    Returns
    -------
    pandana network
    """
    # Defines the x attribute for nodes in the network
    gdf_nodes["x"] = gdf_nodes["geometry"].apply(lambda x: x.x)
    # Defines the y attribute for nodes in the network (e.g. latitude)
    gdf_nodes["y"] = gdf_nodes["geometry"].apply(lambda x: x.y)
    # Defines the node id that begins an edge
    gdf_edges["from"] = gdf_edges["u"].astype(np.int64)
    # Defines the node id that ends an edge
    gdf_edges["to"] = gdf_edges["v"].astype(np.int64)
    # Define the distance based on OpenStreetMap edges
    gdf_edges["length"] = gdf_edges["length"].astype(float)

    gdf_nodes["id"] = gdf_nodes["osmid"].astype(np.int64)
    gdf_nodes.set_index("id", inplace=True, drop=False)
    # Create the transportation network in the city
    # Typical data would be distance based from OSM or travel time from GTFS transit data
    net = pdna.Network(gdf_nodes["x"], gdf_nodes["y"], gdf_edges["from"], gdf_edges["to"], gdf_edges[["length"]])
    # Precomputes the range queries (the reachable nodes within this maximum distance)
    # so that aggregations don’t perform the network queries unnecessarily
    net.precompute(predistance + 10)
    return net


def cal_dist_node_to_nearest_pois(gdf_poi, distance, network, category_field = None, categories = None, filter_field = None, filter_iterations = None,output_names=None,output_prefix=''):
    """
    Calculate the distance from each node to the first nearest destination
    within a given maximum search distance threshold
    If the nearest destination is not within the distance threshold, then it will be coded as -999

    Parameters
    ----------
    gdf_poi: GeoDataFrame
        GeoDataFrame of destination point-of-interest
    distance: int
        the maximum search distance
    network: pandana network
    category_field: str
        a field which if supplied will be iterated over using values from 'categories' list  (default: None)
    categories : list
        list of field names of categories found in category_field (default: None)
    filter_field: str
        a field which if supplied will be iterated over to filter the POI dataframe using a query informed by an expression found in the filter iteration list.  Filters are only applied if a category has not been supplied (ie. use one or the other)  (default: None)
    filter_iterations : list
        list of expressions to query using the filter_field (default: None)
    output_names : list
        list of names which are used to rename the outputs; entries must have corresponding order to categories or filter iterations if these are supplied (default: None)
    output_prefix: str
        option prefix to append to supplied output_names list (default: '')
    
    Returns
    -------
    GeoDataFrame
    """
    gdf_poi["x"] = gdf_poi["geometry"].apply(lambda x: x.x)
    gdf_poi["y"] = gdf_poi["geometry"].apply(lambda x: x.y)
    if category_field is not None and categories is not None:
        # Calculate distances iterating over categories
        appended_data = []
        # establish output names
        if output_names is None:
                output_names = categories
        
        output_names = [f'{output_prefix}{x}' for x in output_names]
        # iterate over each destination category
        for x in categories:
            iteration = categories.index(x)
            # initialize the destination point-of-interest category
            # the positions are specified by the x and y columns (which are Pandas Series)
            # at a max search distance for up to the first nearest points-of-interest
            gdf_poi_filtered = gdf_poi.query(f"{category_field}=='{x}'")
            if len(gdf_poi_filtered) > 0:
                network.set_pois(
                    x,
                    distance,
                    1,
                    gdf_poi_filtered["x"],
                    gdf_poi_filtered["y"],
                )
                # return the distance to the first nearest destination category
                # if zero destination is within the max search distance, then coded as -999
                dist = network.nearest_pois(distance, x, 1, -999)
                
                # change the index name corresponding to each destination name
                dist.columns = dist.columns.astype(str)
                dist.rename(columns={"1": output_names[categories.index(x)]}, inplace=True)
            else:
                dist == pd.DataFrame(index=network.node_ids, columns=output_names[categories.index(x)])
            
            appended_data.append(dist)
        # return a GeoDataFrame with distance to the nearest destination from each source node
        gdf_poi_dist = pd.concat(appended_data, axis=1)
    elif filter_field is not None and filter_iterations is not None:
        # Calculate distances across filtered iterations
        appended_data = []
        # establish output names
        if output_names is None:
            output_names = filter_iterations
        
        output_names = [f'{output_prefix}{x}' for x in output_names]
        # iterate over each destination category
        for x in filter_iterations:
            # initialize the destination point-of-interest category
            # the positions are specified by the x and y columns (which are Pandas Series)
            # at a max search distance for up to the first nearest points-of-interest
            gdf_poi_filtered = gdf_poi.query(f"{filter_field}{x}")
            if len(gdf_poi_filtered) > 0:
                network.set_pois(
                    x,
                    distance,
                    1,
                    gdf_poi_filtered["x"],
                    gdf_poi_filtered["y"],
                )
                # return the distance to the first nearest destination category
                # if zero destination is within the max search distance, then coded as -999
                dist = network.nearest_pois(distance, x, 1, -999)
                
                # change the index name to match desired or default output
                dist.columns = dist.columns.astype(str)
                dist.rename(columns={"1": output_names[filter_iterations.index(x)]}, inplace=True)
            else:
                dist == pd.DataFrame(index=network.node_ids, columns=output_names[categories.index(x)])
            
            appended_data.append(dist)
        # return a GeoDataFrame with distance to the nearest destination from each source node
        gdf_poi_dist = pd.concat(appended_data, axis=1)
    else:
        if output_names is None:
            output_names = ['POI']
        
        output_names = [f'{output_prefix}{x}' for x in output_names]
        network.set_pois(output_names[0], distance, 1, gdf_poi["x"], gdf_poi["y"])
        gdf_poi_dist = network.nearest_pois(distance,output_names[0], 1, -999)
        # change the index name to match desired or default output
        gdf_poi_dist.columns = gdf_poi_dist.columns.astype(str)
        gdf_poi_dist.rename(columns={"1": output_names[0]}, inplace=True)
    
    return gdf_poi_dist


def convert_dist_to_binary(gdf, *columnNames, distance=500):
    """
    Convert numerical distance to binary, 0 or 1
    That is, if the numerical distance is greater than the accessibility distance (default 500),
    then it will be coded as 0, otherwise 1
    
    Parameters
    ----------
    gdf: GeoDataFrame
        GeoDataFrame with distance between nodes and nearest destination
    distance: int
        accessibility distance (default 500m) for evaluating binary indicators
    *columnNames: list
        list of column names of original dist columns and new binary columns
        eg. [[nearest_node_pos_dist], [nearest_node_pos_dist_binary]]
    
    Returns
    -------
    GeoDataFrame
    """
    for x in columnNames:
        # specify original column names with distance, and new binary column name
        columnName = x[0]
        columnBinary = x[1]
        # convert ditance value to 1 if the distance is no greater than the specified accessibility distance
        # indicating that the nearest destination is within the max accessibility distance threshold
        # otherwise,  replace distance value to 0
        gdf[columnBinary] = (gdf[columnName] <= distance).astype("Int64")
    return gdf


def cal_zscores(gdf, oriFieldNames, newFieldNames):
    """
    Calculate z-scores for variables.  Note that the zscore function has a default degrees of freedom of 0 
    (ie. population standard deviation); we want sample standard deviation so we set to dof=1.

    Parameters
    ----------
    gdf: GeoDataFrame
    orifieldNames: list
        list contains origional field names of the columns needed to calculate zscores,
    newfieldNames: list
        list contains new field name after calculate the zscores

    Returns
    -------
    GeoDataFrame
    """
    # zip the old and new field names together
    fieldNames = list(zip(oriFieldNames, newFieldNames))
    for fields in fieldNames:
        # specify original field needed to calculate zscores, and the new field name after zscores
        orifield, newfield = fields[0], fields[1]
        # caluclate zscores within the GeoDataFrame
        gdf[newfield] = gdf[[orifield]].apply(zscore,dof=1)
    return gdf


def create_full_nodes(
    samplePointsData,
    gdf_nodes_simple,
    gdf_nodes_poi_dist,
    distance_names,
    population_density,
    intersection_density,
):
    """
    Create long form working dataset of sample points to evaluate respective node distances and densities
    
    Parameters
    ----------
    samplePointsData: GeoDataFrame
        GeoDataFrame of sample points
    gdf_nodes_simple:  GeoDataFrame
        GeoDataFrame with density records
    gdf_nodes_poi_dist:  GeoDataFrame
        GeoDataFrame of distances to points of interest
    distance_names: list
        List of original distance field names
    population_density: str
        population density variable name
    intersection_density: str
        intersection density variable name
    
    Returns
    -------
    GeoDataFrame
    """
    print("Creating long form working dataset of sample points to evaluate respective node distances and densities")
    full_nodes = samplePointsData[["n1", "n2", "n1_distance", "n2_distance"]].copy()
    print("\t - create long form dataset")
    full_nodes["nodes"] = full_nodes.apply(lambda x: [[int(x.n1), x.n1_distance], [int(x.n2), x.n2_distance]], axis=1)
    full_nodes = full_nodes[["nodes"]].explode("nodes")
    full_nodes[["node", "node_distance_m"]] = pd.DataFrame(full_nodes.nodes.values.tolist(), index=full_nodes.index)
    print("\t - join POIs results from nodes to sample points")
    full_nodes = full_nodes[["node", "node_distance_m"]].join(gdf_nodes_poi_dist, on="node", how="left")
    distance_names = [x for x in distance_names if x in gdf_nodes_poi_dist.columns]
    print("\t - calculate proximity-weighted average of densitiy statistics for each sample point")
    # define aggregation functions for per sample point estimates
    # ie. we take
    #       - minimum of full distances
    #       - and weighted mean of densities
    # The latter is so that if distance from two nodes for a point are 0m and 30m
    #  the weight of 0m is 1 and the weight of 30m is 0.
    #  ie. 1 - (0/(0+30)) = 1    , and 1 - (30/(0+30)) = 0
    #
    # This is not perfect; ideally the densities would be calculated for the sample points directly
    # But it is better than just assigning the value of the nearest node (which may be hundreds of metres away)
    node_weight_denominator = full_nodes["node_distance_m"].groupby(full_nodes.index).sum()
    full_nodes = full_nodes[["node", "node_distance_m"] + distance_names].join(node_weight_denominator, 
                       how="left", rsuffix="_denominator")
    full_nodes["density_weight"] = 1 - (full_nodes["node_distance_m"] / full_nodes["node_distance_m_denominator"])
    # join up full nodes with density fields
    full_nodes = full_nodes.join(gdf_nodes_simple[[population_density, intersection_density]], on="node", how="left")
    full_nodes[population_density] = full_nodes[population_density] * full_nodes.density_weight
    full_nodes[intersection_density] = full_nodes[intersection_density] * full_nodes.density_weight
    new_densities = [population_density, intersection_density]
    agg_functions = dict(
        zip(distance_names + new_densities, ["min"] * len(distance_names) + ["sum"] * len(new_densities))
    )
    full_nodes = full_nodes.groupby(full_nodes.index).agg(agg_functions)
    return full_nodes


def split_list(alist, wanted_parts=1):
    """
    split list

    Parameters
    ----------
    alist: list
        the split list
    wanted_parts: int
        the number of parts (default: {1})

    Returns
    -------
    list
    """
    length = len(alist)
    # return all parts in a list, like [[],[],[]]
    return [alist[i * length // wanted_parts : (i + 1) * length // wanted_parts] for i in range(wanted_parts)]
