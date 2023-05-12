# Script to compute 3D point clouds from depth images donwloaded with recorder_console.py.
#

import argparse
import cv2
from glob import glob
import numpy as np
import os

from recorder_console import read_sensor_poses


# Depth range for short throw and long throw, in meters (approximate)
SHORT_THROW_RANGE = [0.02, 3.]
LONG_THROW_RANGE = [1., 4.]


def save_obj(output_path, points):
    with open(output_path, 'w') as f:
        f.write("# OBJ file\n")
        for v in points:
            f.write("v %.4f %.4f %.4f\n" % (v[0], v[1], v[2]))

def read_obj(path):
    with open(path, 'r') as f:        
        # get lines
        lines = f.readlines()
        
        # create empty points
        nlines = len(lines)
        points = np.zeros( (nlines,3) )
        
        # get points
        i = 0
        for line in lines: 
            elem = line.split()

            # skip header lines
            if elem[0] == '#':
                continue
            
            # elem[0] should be 'v'
            if elem[0] == 'v':
                points[i,0] = elem[1]
                points[i,1] = elem[2]
                points[i,2] = elem[3]
                i = i+1
        
        # remove header and empty lines 
        if i < nlines:
            diff = nlines - i
            points = points[:-diff]
        
        return points

def parse_projection_bin(path, w, h):
    # See repo issue #63
    # Read binary file
    projection = np.fromfile(path, dtype=np.float32)
    x_list = [ projection[i] for i in range(0, len(projection), 2) ]
    y_list = [ projection[i] for i in range(1, len(projection), 2) ]
    
    u = np.asarray(x_list).reshape(w, h).T
    v = np.asarray(y_list).reshape(w, h).T

    return [u, v]


def pgm2distance(img, encoded=False):
    # See repo issue #19
    img.byteswap(inplace=True)
    return img.astype(np.float)/1000.0


def get_points(img, us, vs, cam2world, depth_range):
    distance_img = pgm2distance(img, encoded=False)

    if cam2world is not None:
        R = cam2world[:3, :3]
        t = cam2world[:3, 3]
    else:
        R, t = np.eye(3), np.zeros(3)

    points = []
    for i in np.arange(distance_img.shape[0]):
        for j in np.arange(distance_img.shape[1]):
            x = us[i, j]
            y = vs[i, j]
            
            # Compute Z values as described in issue #63
            # https://github.com/Microsoft/HoloLensForCV/issues/63#issuecomment-429469425
            D = distance_img[i, j]
            z = - float(D) / np.sqrt(x*x + y*y + 1)
            
            if np.isinf(x) or np.isinf(y) or \
               D < depth_range[0] or D > depth_range[1]:
                continue
            
            # 3D point in camera coordinate system
            point = np.array([x, y, 1.]) * z
            
            # Camera to World
            point = np.dot(R, point) + t

            # Append point
            points.append(point)
   
    return np.vstack(points) if points else np.array(points)


def get_cam2world(path, sensor_poses):
    time_stamp = int(os.path.splitext(os.path.basename(path))[0])
    if time_stamp not in sensor_poses.keys():
        return None
    world2cam = sensor_poses[time_stamp]
    return np.linalg.inv(world2cam)


def process_folder(args, cam):
    # Input folder
    folder = args.workspace_path
    cam_folder = os.path.join(folder, cam)
    assert(os.path.exists(cam_folder))

    # Output folder
    output_folder = os.path.join(args.output_path, cam)
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # Get camera projection info
    bin_path = os.path.join(
        args.workspace_path, f"{cam}_camera_space_projection.bin"
    )

    # From frame to world coordinate system
    sensor_poses = None
    if not args.ignore_sensor_poses:
        sensor_poses = read_sensor_poses(
            os.path.join(folder, f"{cam}.csv"), identity_camera_to_image=True
        )        

    # Get appropriate depth thresholds
    depth_range = LONG_THROW_RANGE if 'long' in cam else SHORT_THROW_RANGE

    # Get depth paths
    depth_paths = sorted(glob(os.path.join(cam_folder, "*pgm")))
    if args.max_num_frames == -1:
        args.max_num_frames = len(depth_paths)
    depth_paths = depth_paths[args.start_frame:(args.start_frame + args.max_num_frames)]    

    # Process paths
    merge_points = args.merge_points
    overwrite    = args.overwrite
    use_cache    = args.use_cache
    points_merged = []
    us = vs = None
    for i_path, path in enumerate(depth_paths):
        output_suffix = f"_{args.output_suffix}" if len(args.output_suffix) else ""
        pcloud_output_path = os.path.join(
            output_folder,
            os.path.basename(path).replace(".pgm", f"{output_suffix}.obj"),
        )
        print("Progress file (%d/%d): %s" %
              (i_path+1, len(depth_paths), pcloud_output_path))

        # if file exist
        output_file_exist = os.path.exists(pcloud_output_path)
        if output_file_exist and use_cache:
            points = read_obj(pcloud_output_path)
        else:
            img = cv2.imread(path, -1)
            if us is None or vs is None:
                us, vs = parse_projection_bin(bin_path, img.shape[1], img.shape[0])
            cam2world = get_cam2world(path, sensor_poses) if sensor_poses is not None else None
            points = get_points(img, us, vs, cam2world, depth_range)  

        if merge_points:
            points_merged.extend(points)

        if not output_file_exist or overwrite:
            save_obj(pcloud_output_path, points)

    return points_merged


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace_path", required=True, help="Path to workspace folder used for downloading")
    parser.add_argument("--output_path", required=False, help="Path to output folder where to save the point clouds. By default, equal to output_path")
    parser.add_argument("--output_suffix", required=False, default="", help="If a suffix is specified, point clouds will be saved as [tstamp]_[suffix].obj")
    parser.add_argument("--short_throw", action='store_true', help="Extract point clouds from short throw frames")
    parser.add_argument("--long_throw", action='store_true', help="Extract point clouds from long throw frames")
    parser.add_argument("--ignore_sensor_poses", action='store_true', help="Drop HL pose information (point clouds will not be aligned to a common ref space)")
    parser.add_argument("--start_frame", type=int, default=0)
    parser.add_argument("--max_num_frames", type=int, default=-1)
    parser.add_argument("--merge_points",  action='store_true', default=False, help="Save file with all the points (in world coordinate system)") 
    parser.add_argument("--use_cache", action='store_true', default=False, help="Load already existing files") 
    parser.add_argument("--overwrite", action='store_true', default=False, help="Write output files (overwrite if exist).")

    args = parser.parse_args()

    if (not args.short_throw) and (not args.long_throw):
        print("At least one between short_throw and long_throw must be set to true.\
                Please pass \"--short_throw\" and/or \"--long_throw\" as parameter.")
        exit()
    
    assert(os.path.exists(args.workspace_path))    
    if args.output_path is None:
        args.output_path = args.workspace_path

    return args


def main():
    # read options
    args = parse_args()
    if args.short_throw:        
        camera = 'short_throw_depth'
    if args.long_throw:
        camera = 'long_throw_depth'

    # process
    print(f"Processing '{camera}' depth folder...")
    points = process_folder(args, camera)
    print('Done processing.')

    # save output
    if args.merge_points:
        output_folder = os.path.join(args.output_path, camera)
        output_filename = f"{output_folder}.obj"
        print(f"Saving file with all points: {output_filename}")
        save_obj(output_filename, points)

    print("Done.")

if __name__ == "__main__":    
    main()
